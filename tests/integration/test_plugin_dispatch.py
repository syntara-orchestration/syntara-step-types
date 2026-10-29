"""Dispatch real gRPC calls using identities compiled from the shipped plugin descriptors."""

from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest
from syntara_node_http_request import HttpOutput
from syntara_plugin.contracts import canonical_json_digest
from syntara_plugin.runtime import add_container_runtime_service, step_registry_from_bindings
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc
from syntara_plugin.sdk import BuildRequest, compile_workspace

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("step", "module_name", "inputs", "expected"),
    [
        (
            "script",
            "syntara_node_script.__main__",
            {"language": "python", "code": 'print("dispatched")'},
            {"stdout": "dispatched\n"},
        ),
        (
            "script",
            "syntara_node_script.__main__",
            {"language": "bash", "code": "printf dispatched"},
            {"stdout": "dispatched"},
        ),
        (
            "http-request",
            "syntara_node_http_request.__main__",
            {"url": "https://example.com/"},
            {"body": {"dispatched": True}},
        ),
    ],
)
def test_execute_using_compiled_plugin_target(step, module_name, inputs, expected, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    compiled = compile_workspace(
        BuildRequest(
            root_manifest=root / step / "plugin.yaml",
            image_bindings={f"{step}-runner": f"localhost/syntara/{step}@sha256:" + "a" * 64},
        )
    )
    target = compiled.descriptor["spec"]["targets"][0]
    bootstrap = importlib.import_module(module_name)
    registry = step_registry_from_bindings(bootstrap.create_bindings(root / step / "manifest.yaml"))
    # Only the remote HTTP call is replaced. Identity, schemas, binding and gRPC dispatch are real.
    monkeypatch.setattr(
        "syntara_node_http_request.execute_http_request",
        lambda *args: HttpOutput(status_code=200, body={"dispatched": True}, headers={}, elapsed=0),
    )
    output_digest = canonical_json_digest(target["output"])
    with ThreadPoolExecutor(max_workers=10) as dispatch:
        server = grpc.server(dispatch)
        add_container_runtime_service(server, registry)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = container_pb2_grpc.ContainerServiceStub(channel)
                assert (
                    target["identity"]
                    in stub.Health(container_pb2.HealthRequest(), timeout=1).step_revision_ids
                )
                request = container_pb2.ExecuteRequest(
                    invocation_id=f"compiled-{step}-{inputs.get('language', 'http')}",
                    workflow_run_id="wf-compiled-target",
                    step_revision_id=target["identity"],
                    runtime_abi="syntara.container/v1alpha1",
                    inputs_json=json.dumps(inputs).encode(),
                    runtime_context_json=json.dumps({"outputSchemaDigest": output_digest}).encode(),
                    credential_file_paths_json=b"{}",
                    timeout_seconds=5,
                )
                events = list(stub.Execute(request, timeout=5))
                assert len(events) == 1
                assert events[0].HasField("result"), events[0]
                assert events[0].result.output_schema_digest == output_digest
                output = json.loads(events[0].result.output_json)
                for key, value in expected.items():
                    assert output[key] == value
        finally:
            server.stop(0).wait()
