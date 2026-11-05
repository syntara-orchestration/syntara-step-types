"""Preserve failed script logs through the actual language-specific SDK bindings."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest
from syntara_node_script import bash, python
from syntara_plugin.runtime import add_container_runtime_service, step_registry_from_bindings
from syntara_plugin.runtime.protocol import container_pb2 as pb
from syntara_plugin.runtime.protocol import container_pb2_grpc as rpc


@pytest.mark.parametrize(
    ("module", "plugin", "code"),
    [
        (bash, "script-bash", "echo before; echo problem >&2; exit 7"),
        (
            python,
            "script-python",
            'import sys; print("before"); print("problem", file=sys.stderr); sys.exit(7)',
        ),
    ],
)
def test_failed_script_error_result_preserves_logs_over_grpc(module, plugin, code):
    manifest = Path(__file__).resolve().parents[2] / plugin / "manifest.yaml"
    with ThreadPoolExecutor(max_workers=4) as pool:
        server = grpc.server(pool)
        add_container_runtime_service(
            server, step_registry_from_bindings(module.create_bindings(manifest))
        )
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = rpc.ContainerServiceStub(channel)
                request = pb.ExecuteRequest(
                    invocation_id="failed-script",
                    workflow_run_id="test-run",
                    step_revision_id=module.STEP_IDENTITY,
                    runtime_abi="syntara.container/v1alpha1",
                    inputs_json=json.dumps({"code": code}).encode(),
                    credential_file_paths_json=b"{}",
                    runtime_context_json=b"{}",
                    timeout_seconds=5,
                )
                events = list(stub.Execute(request, timeout=10))
                assert not any(event.HasField("failure") for event in events)
                results = [event.result for event in events if event.HasField("result")]
                assert len(results) == 1
                assert results[0].status_code != 0
                error = json.loads(results[0].error_result_json)
                assert error["code"] == "REMOTE_REJECTED"
                assert error["category"] == "business"
                assert error["retryable"] is False
                assert error["return_code"] == 7
                assert error["stdout"] == "before\n"
                # macOS gRPC can also write fork diagnostics in the child.
                assert error["stderr"].endswith("problem\n")
        finally:
            server.stop(0).wait()
