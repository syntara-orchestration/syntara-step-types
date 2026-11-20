"""Serve the agent runtime Action over the SDK container protocol."""

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

from syntara_node_agent_runtime import AgentRuntimeStep

STEP_IDENTITY = "syntara.agent-runtime.agent-runtime"
GRPC_ADDRESS = "0.0.0.0:50051"


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
    step = AgentRuntimeStep()
    bindings = StepBindings()
    bindings.bind(StepDescriptor.for_installed_step(step, installed), step)
    return bindings


def main() -> None:
    """Start the SDK gRPC service and serve until shutdown."""
    logging.basicConfig(level=logging.INFO)
    registry = step_registry_from_bindings(create_bindings())
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    add_container_runtime_service(server, registry)
    if server.add_insecure_port(GRPC_ADDRESS) == 0:
        raise RuntimeError("Could not bind the SDK container listener.")
    server.start()

    def shutdown(_signal: int, _frame: FrameType | None) -> None:
        server.stop(5)

    signal.signal(signal.SIGTERM, shutdown)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
