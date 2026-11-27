"""Serve the AO task agent Action over the SDK container protocol."""

from __future__ import annotations

from pathlib import Path

import yaml
from syntara_plugin.runtime import (
    InstalledStepManifest,
    StepBindings,
    StepDescriptor,
)
from syntara_plugin.runtime.server import main as serve_runtime

from syntara_node_ao_task_agent import AOTaskAgentStep

STEP_IDENTITY = "syntara.ao-task-agent.ao-task-agent"


def create_bindings(manifest_path: Path | None = None) -> StepBindings:
    """Build the installed binding served by this immutable image."""
    if manifest_path is None:
        installed_manifest = Path(__file__).with_name("manifest.yaml")
        manifest_path = (
            installed_manifest
            if installed_manifest.is_file()
            else Path(__file__).parents[2] / "manifest.yaml"
        )
    path = manifest_path
    with path.open(encoding="utf-8") as stream:
        manifest = yaml.safe_load(stream)
    installed = InstalledStepManifest.from_manifest(
        step_identity=STEP_IDENTITY,
        manifest=manifest,
        assets={},
        credential_targets=[],
    )
    step = AOTaskAgentStep()
    bindings = StepBindings()
    bindings.bind(StepDescriptor.for_installed_step(step, installed), step)
    return bindings


def main() -> None:
    """Start the SDK gRPC service and serve until shutdown."""
    serve_runtime()


if __name__ == "__main__":
    main()
