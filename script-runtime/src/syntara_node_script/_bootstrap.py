"""Shared container bootstrap for the language-specific script images."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from syntara_plugin.runtime import (
    ActionStep,
    InstalledStepManifest,
    StepBindings,
    StepDescriptor,
)


def create_bindings_for(
    step_identity: str,
    step: ActionStep[Any, Any],
    manifest_path: Path | None = None,
) -> StepBindings:
    if manifest_path is None:
        manifest_path = Path("/app/manifest.yaml")
    with manifest_path.open() as manifest_file:
        manifest_doc = yaml.safe_load(manifest_file)

    installed_step = InstalledStepManifest.from_manifest(
        step_identity=step_identity,
        manifest=manifest_doc,
        assets={},
    )
    descriptor = StepDescriptor.for_installed_step(step, installed_step)
    bindings = StepBindings()
    bindings.bind(descriptor, step)
    return bindings
