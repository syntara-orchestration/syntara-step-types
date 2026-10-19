"""Container entrypoint: starts the gRPC ContainerService for the HTTP request step."""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from types import FrameType

import grpc
from syntara_plugin.runtime import (
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
    bindings = StepBindings()
    step = HttpRequestStep()
    bindings.bind(StepDescriptor.for_step(STEP_IDENTITY, step), step)
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
