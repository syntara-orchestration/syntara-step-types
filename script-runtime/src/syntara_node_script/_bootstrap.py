"""Shared container bootstrap for the language-specific script images."""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import grpc
import yaml
from syntara_plugin.runtime import (
    ActionStep,
    InstalledStepManifest,
    StepBindings,
    StepDescriptor,
    add_container_runtime_service,
    step_registry_from_bindings,
)


def create_bindings_for(
    step_identity: str,
    step: ActionStep[Any, Any],
    manifest_path: Path | None = None,
) -> StepBindings:
    if manifest_path is None:
        manifest_path = Path("/app/manifest.yaml")
    with manifest_path.open() as manifest_file:
        manifest_doc = yaml.safe_load(manifest_file)

    installed_step = InstalledStepManifest.from_manifest(
        step_identity=step_identity,
        manifest=manifest_doc,
        assets={},
    )
    descriptor = StepDescriptor.for_installed_step(step, installed_step)
    bindings = StepBindings()
    bindings.bind(descriptor, step)
    return bindings


def serve(step_identity: str, bindings: StepBindings) -> None:
    logging.basicConfig(level=logging.INFO)
    registry = step_registry_from_bindings(bindings)
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    add_container_runtime_service(server, registry)
    server.add_insecure_port("0.0.0.0:50051")
    server.start()
    logging.info("Script step server started on 0.0.0.0:50051 (identity=%s)", step_identity)

    def _shutdown(sig: int, frame: object) -> None:
        server.stop(5)

    signal.signal(signal.SIGTERM, _shutdown)
    server.wait_for_termination()
