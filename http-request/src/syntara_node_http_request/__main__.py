"""Container entrypoint: starts the gRPC ContainerService for the HTTP request step."""

from __future__ import annotations

from pathlib import Path

import yaml
from syntara_plugin.runtime import (
    InstalledStepManifest,
    StepBindings,
    StepDescriptor,
)
from syntara_plugin.runtime.server import main as serve_runtime

from syntara_node_http_request import HttpRequestStep

STEP_IDENTITY = "syntara.http-request.http-request"


def create_bindings(manifest_path: Path | None = None) -> StepBindings:
    """Build the exact installed bindings used by the container entrypoint."""
    if manifest_path is None:
        manifest_path = Path(__file__).parent / "manifest.yaml"
        if not manifest_path.exists():
            manifest_path = Path(__file__).parents[2] / "manifest.yaml"
    with manifest_path.open() as f:
        manifest_doc = yaml.safe_load(f)

    # Create installed step manifest from the loaded document
    installed_step = InstalledStepManifest.from_manifest(
        step_identity=STEP_IDENTITY,
        manifest=manifest_doc,
        assets={},  # No external assets for inline schemas
        credential_targets=[
            yaml.safe_load((Path(__file__).parent / target).read_text())
            for target in ("integration.yaml", "bearer-credential.yaml")
        ],
    )

    # Bind using the installed Step manifest
    bindings = StepBindings()
    step = HttpRequestStep()
    descriptor = StepDescriptor.for_installed_step(step, installed_step)
    bindings.bind(descriptor, step)

    return bindings


def main() -> None:
    serve_runtime()


if __name__ == "__main__":
    main()
