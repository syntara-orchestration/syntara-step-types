"""Container entry point for the AAP job template step."""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import FrameType

import grpc
import yaml
from syntara_plugin.runtime import (
    InstalledStepManifest,
    StepBindings,
    StepDescriptor,
    add_container_runtime_service,
    step_registry_from_bindings,
)

from . import AAPJobTemplateStep

STEP_IDENTITY = "syntara.aap-job-template.aap-job-template"
GRPC_ADDRESS = "0.0.0.0:50051"


def create_bindings(manifest_path: Path | None = None) -> StepBindings:
    """Load the installed Action and bind its step implementation."""
    if manifest_path is None:
        manifest_path = Path(__file__).parent / "manifest.yaml"
        if not manifest_path.exists():
            manifest_path = Path(__file__).parents[2] / "manifest.yaml"
    with manifest_path.open() as file:
        manifest_doc = yaml.safe_load(file)
    credential_targets = [
        yaml.safe_load((Path(__file__).parent / target).read_text())
        for target in ("integration.yaml", "aap-credential.yaml")
    ]
    installed_step = InstalledStepManifest.from_manifest(
        step_identity=STEP_IDENTITY,
        manifest=manifest_doc,
        assets={},
        credential_targets=credential_targets,
    )
    step = AAPJobTemplateStep()
    descriptor = StepDescriptor.for_installed_step(step, installed_step)
    bindings = StepBindings()
    bindings.bind(descriptor, step)
    return bindings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    registry = step_registry_from_bindings(create_bindings())
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    add_container_runtime_service(server, registry)
    server.add_insecure_port(GRPC_ADDRESS)
    server.start()
    logging.info(
        "AAP job template step server started on %s (identity=%s)",
        GRPC_ADDRESS,
        STEP_IDENTITY,
    )

    def shutdown(_sig: int, _frame: FrameType | None) -> None:
        server.stop(5)

    signal.signal(signal.SIGTERM, shutdown)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
