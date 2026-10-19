"""Container entrypoint: starts the gRPC ContainerService for the script step."""
from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor

import grpc
from syntara_plugin.runtime import (
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
    bindings = StepBindings()
    step = ScriptStep()
    bindings.bind(StepDescriptor.for_step(STEP_IDENTITY, step), step)
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
