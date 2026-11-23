"""Container entrypoint: starts the gRPC ContainerService for the AAP Workflow step.

Runtime model per docs/aap-workflow-sdk-verification.md: a cold-start,
single-invocation pod running the SDK synchronous gRPC runtime. Dev/test runs
use an insecure port; the production launcher wires mutual TLS through the
SDK's TLS environment contract (tracked with AAP-98873/AAP-98908).
"""

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

from syntara_node_aap_workflow import AapWorkflowStep

STEP_IDENTITY = "syntara.aap.aap-workflow"
GRPC_ADDRESS = "0.0.0.0:50051"


def create_bindings(manifest_path: Path | None = None) -> StepBindings:
    """Build the exact installed bindings used by the container entrypoint."""
    if manifest_path is None:
        manifest_path = Path(__file__).parent / "manifest.yaml"
        if not manifest_path.exists():
            manifest_path = Path(__file__).parents[2] / "manifest.yaml"
    with manifest_path.open() as f:
        manifest_doc = yaml.safe_load(f)

    installed_step = InstalledStepManifest.from_manifest(
        step_identity=STEP_IDENTITY,
        manifest=manifest_doc,
        assets={},  # No external assets for inline schemas
        credential_targets=[
            yaml.safe_load((Path(__file__).parent / target).read_text())
            for target in ("integration.yaml", "aap-credential.yaml")
        ],
    )

    bindings = StepBindings()
    step = AapWorkflowStep()
    descriptor = StepDescriptor.for_installed_step(step, installed_step)
    bindings.bind(descriptor, step)

    return bindings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    registry = step_registry_from_bindings(create_bindings())

    # Reserve enough threads so Cancel and Health RPCs can always get a thread,
    # even when the maximum number of concurrent Execute calls are in flight.
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    add_container_runtime_service(server, registry)
    server.add_insecure_port(GRPC_ADDRESS)
    server.start()
    logging.info(
        "AAP Workflow step server started on %s (identity=%s)", GRPC_ADDRESS, STEP_IDENTITY
    )

    def _shutdown(sig: int, frame: FrameType | None) -> None:
        server.stop(5)

    signal.signal(signal.SIGTERM, _shutdown)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
