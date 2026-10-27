"""Dev-only container entrypoint for the script-step workload image.

Serves the SDK's container runtime on an insecure port so the Execution
Plane — which currently dials an insecure channel through the API-server
port-forward — can reach it. The standard ``syntara_plugin.runtime.server``
entrypoint requires mutual TLS for non-loopback listeners and is the
production path once the plane's transport gains TLS.
"""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor

import grpc
from syntara_plugin.runtime import add_container_runtime_service, step_registry_from_bindings

from script_step.runtime import create_bindings

GRPC_ADDRESS = "0.0.0.0:50051"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bindings = create_bindings()
    registry = step_registry_from_bindings(bindings)
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    add_container_runtime_service(server, registry)
    server.add_insecure_port(GRPC_ADDRESS)
    server.start()
    logging.info("Script step server started on %s (steps: %s)", GRPC_ADDRESS, ", ".join(bindings.step_identities))

    def _shutdown(sig: int, frame: object) -> None:  # noqa: ARG001
        server.stop(5)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
