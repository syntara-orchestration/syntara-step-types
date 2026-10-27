"""Script step types and installed-manifest bindings.

Two Action step types share one workload image and one subprocess executor:
``PythonScriptStep`` and ``BashScriptStep`` differ in metadata (identity,
display name, input/output schemas) and in the interpreter they drive.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Mapping
from importlib.resources import files
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field
from syntara_plugin.runtime import (
    ActionStep,
    BaseStep,
    ContainerInvocationError,
    ExecutionContext,
    FailureCode,
    InstalledStepManifest,
    StepBindings,
    StepDescriptor,
)

from .executor import (
    ScriptCancelledError,
    ScriptExecutionError,
    parse_stdout_json,
    run_script_async,
)


class PythonScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)


class PythonScriptOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_code: int
    stdout: str
    stderr: str
    stdout_json: Any | None = None


class PythonScriptStep(ActionStep[PythonScriptInput, PythonScriptOutput]):
    """Runs Python 3 code in an isolated subprocess."""

    def run(self, inputs: PythonScriptInput, context: ExecutionContext) -> PythonScriptOutput:
        result = _run_script("python", inputs, context)
        return PythonScriptOutput(**result, stdout_json=parse_stdout_json("python", result["stdout"]))


class BashScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)


class BashScriptOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_code: int
    stdout: str
    stderr: str


class BashScriptStep(ActionStep[BashScriptInput, BashScriptOutput]):
    """Runs a Bash script in an isolated subprocess."""

    def run(self, inputs: BashScriptInput, context: ExecutionContext) -> BashScriptOutput:
        result = _run_script("bash", inputs, context)
        return BashScriptOutput(**result)


def _run_script(language: str, inputs: PythonScriptInput | BashScriptInput, context: ExecutionContext) -> dict[str, Any]:
    try:
        return asyncio.run(
            run_script_async(
                language,
                inputs.code,
                dict(inputs.environment),
                cancelled=context.cancellation.is_set,
            )
        )
    except ScriptCancelledError as exc:
        raise ContainerInvocationError(
            FailureCode.CANCELLED,
            "Script execution was cancelled",
            retryable=False,
            outcome="not_completed",
        ) from exc
    except ScriptExecutionError as exc:
        raise ContainerInvocationError(
            FailureCode.REMOTE_REJECTED,
            f"Script exited with code {exc.exit_code}",
            retryable=False,
            outcome="not_completed",
        ) from exc


def create_bindings() -> StepBindings:
    """Load every packaged manifest and bind its declared Python entry point."""

    root = _read_manifest("plugin.yaml")
    metadata = _mapping(root.get("metadata"), "plugin.metadata")
    specification = _mapping(root.get("spec"), "plugin.spec")
    namespace = _identifier(metadata.get("namespace"), "plugin.metadata.namespace")
    plugin_name = _identifier(metadata.get("name"), "plugin.metadata.name")
    target_paths = specification.get("targets")
    if not isinstance(target_paths, list) or not target_paths:
        raise RuntimeError("plugin.spec.targets must be a non-empty list")

    bindings = StepBindings()
    for target_path in target_paths:
        if not isinstance(target_path, str):
            raise RuntimeError("plugin.spec.targets entries must be strings")
        document = _read_manifest(target_path)
        target_metadata = _mapping(document.get("metadata"), f"{target_path}.metadata")
        target_specification = _mapping(document.get("spec"), f"{target_path}.spec")
        target_runtime = _mapping(
            target_specification.get("runtime"), f"{target_path}.spec.runtime"
        )
        entry_point = target_runtime.get("entryPoint")
        if not isinstance(entry_point, str):
            raise RuntimeError(f"{target_path}.spec.runtime.entryPoint must be a string")
        step = _load_step(entry_point)()
        identity = (
            f"{namespace}.{plugin_name}."
            f"{_identifier(target_metadata.get('name'), f'{target_path}.metadata.name')}"
        )
        assets = _declared_assets(target_specification, target_path)
        installed_step = InstalledStepManifest.from_manifest(identity, document, assets)
        bindings.bind(
            StepDescriptor.for_installed_step(step, installed_step),
            step,
        )
    return bindings


def _declared_assets(specification: Mapping[str, Any], target_path: str) -> dict[str, Any]:
    """Load every asset-backed schema a target declares, keyed by its path."""

    assets: dict[str, Any] = {}
    for field in ("input", "output", "error"):
        source = specification.get(field)
        if isinstance(source, Mapping) and source.get("kind") == "asset":
            path = source.get("path")
            if isinstance(path, str) and path:
                assets[path] = _read_asset(path, target_path)
    return assets


def _read_asset(relative_path: str, target_path: str) -> dict[str, Any]:
    resource = files("script_step").joinpath("manifests", *relative_path.split("/"))
    try:
        document = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeError(f"could not read packaged asset {relative_path!r} for {target_path!r}") from error
    return _mapping(document, relative_path)


def _read_manifest(relative_path: str) -> dict[str, Any]:
    resource = files("script_step").joinpath("manifests", *relative_path.split("/"))
    try:
        document = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeError(f"could not read packaged manifest {relative_path!r}") from error
    return _mapping(document, relative_path)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return dict(value)


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} must be a non-empty string")
    return value


def _load_step(entry_point: str) -> type[BaseStep[Any, Any]]:
    module_name, separator, class_name = entry_point.partition(":")
    if not module_name or not separator or not class_name:
        raise RuntimeError(f"invalid step entry point {entry_point!r}")
    try:
        step_class = getattr(importlib.import_module(module_name), class_name, None)
    except ImportError as error:
        raise RuntimeError(f"could not import step entry point {entry_point!r}") from error
    if not isinstance(step_class, type) or not issubclass(step_class, BaseStep):
        raise RuntimeError(f"step entry point {entry_point!r} must name a BaseStep subclass")
    return cast(type[BaseStep[Any, Any]], step_class)
