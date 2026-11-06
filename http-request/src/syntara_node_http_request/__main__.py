"""Container entrypoint: starts the gRPC ContainerService for the HTTP request step."""

from __future__ import annotations

import json
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

_REGISTRATION_MANIFEST = Path("/opt/syntara/registration-manifest.json")
_FALLBACK_IDENTITY = "syntara.step.http-request:0.1.0"
GRPC_ADDRESS = "0.0.0.0:50051"


def _step_identity() -> str:
    """Derive the step identity from the image's registration manifest.

    The execution plane validates that the workload's Health response
    advertises the step revision the platform selected, which is the
    ``stepIdentity`` the plugin build bakes into the image. Fall back to a
    local default when the manifest is absent (e.g. bare local runs).
    """
    try:
        document = json.loads(_REGISTRATION_MANIFEST.read_text(encoding="utf-8"))
        steps = document.get("steps")
        if isinstance(steps, dict):
            for step in steps.values():
                identity = step.get("stepIdentity") if isinstance(step, dict) else None
                if isinstance(identity, str) and identity:
                    return identity
    except (OSError, json.JSONDecodeError):
        pass
    return _FALLBACK_IDENTITY


STEP_IDENTITY = _step_identity()


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
