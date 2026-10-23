"""Container entrypoint: starts the gRPC ContainerService for the script step."""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import yaml
from syntara_plugin.runtime import (
    InstalledActionManifest,
    StepBindings,
    StepDescriptor,
    add_container_runtime_service,
    step_registry_from_bindings,
)

from syntara_node_script import ScriptStep

STEP_IDENTITY = "syntara.step.script:0.1.0"
GRPC_ADDRESS = "0.0.0.0:50051"


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Load the installed Action manifest
    manifest_path = Path(__file__).parent / "manifest.yaml"
    with manifest_path.open() as f:
        manifest_doc = yaml.safe_load(f)

    # Create installed action manifest from the loaded document
    installed_action = InstalledActionManifest.from_manifest(
        step_identity=STEP_IDENTITY,
        manifest=manifest_doc,
        assets={},  # No external assets for inline schemas
    )

    # Bind using the installed Action manifest
    bindings = StepBindings()
    step = ScriptStep()
    descriptor = StepDescriptor.for_installed_action(step, installed_action)
    bindings.bind(descriptor, step)

    registry = step_registry_from_bindings(bindings)
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    add_container_runtime_service(server, registry)
    server.add_insecure_port(GRPC_ADDRESS)
    server.start()
    logging.info("Script step server started on %s (identity=%s)", GRPC_ADDRESS, STEP_IDENTITY)

    def _shutdown(sig: int, frame: object) -> None:
        server.stop(5)

    signal.signal(signal.SIGTERM, _shutdown)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
