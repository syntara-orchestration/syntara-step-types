"""Container entrypoint: starts the gRPC ContainerService for the HTTP request step."""

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

from syntara_node_http_request import HttpRequestStep

STEP_IDENTITY = "syntara.step.http-request:0.1.0"
GRPC_ADDRESS = "0.0.0.0:50051"


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Load the installed Step manifest
    manifest_path = Path(__file__).parent / "manifest.yaml"
    with manifest_path.open() as f:
        manifest_doc = yaml.safe_load(f)

    # Create installed step manifest from the loaded document
    installed_step = InstalledStepManifest.from_manifest(
        step_identity=STEP_IDENTITY,
        manifest=manifest_doc,
        assets={},  # No external assets for inline schemas
    )

    # Bind using the installed Step manifest
    bindings = StepBindings()
    step = HttpRequestStep()
    descriptor = StepDescriptor.for_installed_step(step, installed_step)
    bindings.bind(descriptor, step)

    registry = step_registry_from_bindings(bindings)

    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    add_container_runtime_service(server, registry)
    server.add_insecure_port(GRPC_ADDRESS)
    server.start()
    logging.info(
        "HTTP request step server started on %s (identity=%s)", GRPC_ADDRESS, STEP_IDENTITY
    )

    def _shutdown(sig: int, frame: FrameType | None) -> None:
        server.stop(5)

    signal.signal(signal.SIGTERM, _shutdown)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
