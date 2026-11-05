"""Exercise completed script failures through the image's actual SDK binding."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest
from syntara_node_script.__main__ import STEP_IDENTITY, create_bindings
from syntara_plugin.runtime import (
    ContainerClient,
    ContainerInvocation,
    ContainerResult,
    add_container_runtime_service,
    step_registry_from_bindings,
)


@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("bash", "echo before; echo problem >&2; exit 7"),
        ("python", 'import sys; print("before"); print("problem", file=sys.stderr); sys.exit(7)'),
    ],
)
def test_failed_script_result_preserves_logs_over_grpc(language: str, code: str) -> None:
    bindings = create_bindings(Path(__file__).resolve().parents[1] / "manifest.yaml")
    with ThreadPoolExecutor(max_workers=4) as pool:
        server = grpc.server(pool)
        add_container_runtime_service(server, step_registry_from_bindings(bindings))
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                client = ContainerClient(channel)
                assert STEP_IDENTITY in client.health(timeout_seconds=2)
                invocation = ContainerInvocation(
                    invocation_id="failed-script",
                    workflow_run_id="test-run",
                    step_revision_id=STEP_IDENTITY,
                    inputs={"language": language, "code": code},
                    credential_file_paths={},
                    timeout_seconds=5,
                )
                results = [
                    event
                    for event in client.stream(invocation)
                    if isinstance(event, ContainerResult)
                ]
                assert len(results) == 1
                output = results[0].output
                assert output is not None
                assert output["return_code"] == 7
                assert output["stdout"] == "before\n"
                # macOS gRPC can also write fork diagnostics in the child.
                assert output["stderr"].endswith("problem\n")
                assert output["stdout_json"] is None
        finally:
            server.stop(0).wait()
