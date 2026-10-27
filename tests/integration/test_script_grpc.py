"""Integration tests for the script step container via gRPC."""

from __future__ import annotations

import json

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

pytestmark = pytest.mark.integration

_STEP_REVISION_ID = "syntara.step.script:0.1.0"
_RUNTIME_ABI = "syntara.container/v1alpha1"


def _execute(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    language: str = "python",
    code: str,
    environment: dict[str, str] | None = None,
    invocation_id: str = "test-001",
) -> dict:
    request = container_pb2.ExecuteRequest(
        invocation_id=invocation_id,
        workflow_run_id="test-wf-001",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps(
            {"language": language, "code": code, "environment": environment or {}}
        ).encode(),
        runtime_context_json=b"{}",
        credential_file_paths_json=b"{}",
        timeout_seconds=30,
    )
    for event in stub.Execute(request, timeout=35):
        if event.HasField("result"):
            return json.loads(event.result.output_json)
        if event.HasField("failure"):
            pytest.fail(
                f"Step returned failure — code={event.failure.code} "
                f"message={event.failure.message}"
            )
    pytest.fail("Execute stream closed without a result or failure event")


def test_health_check(script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    response = script_stub.Health(container_pb2.HealthRequest())
    assert response.ready
    assert _STEP_REVISION_ID in list(response.step_revision_ids)


def test_python_print(script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(script_stub, code='print("hello from python")')
    assert result["return_code"] == 0
    assert "hello from python" in result["stdout"]


def test_bash_echo(script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(script_stub, language="bash", code='echo "hello from bash"')
    assert result["return_code"] == 0
    assert "hello from bash" in result["stdout"]


def test_environment_variables(script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        script_stub,
        code="import os; print(os.environ['MY_VAR'])",
        environment={"MY_VAR": "hello-env"},
    )
    assert result["return_code"] == 0
    assert "hello-env" in result["stdout"]


def test_json_output_parsing(script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        script_stub,
        code='import json; print(json.dumps({"status": "ok", "value": 42}))',
    )
    assert result["return_code"] == 0
    assert result["stdout_json"] == {"status": "ok", "value": 42}


def test_script_failure_handling(script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    request = container_pb2.ExecuteRequest(
        invocation_id="test-fail-001",
        workflow_run_id="test-wf-001",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps(
            {"language": "python", "code": "raise SystemExit(1)", "environment": {}}
        ).encode(),
        runtime_context_json=b"{}",
        credential_file_paths_json=b"{}",
        timeout_seconds=30,
    )
    for event in script_stub.Execute(request, timeout=35):
        if event.HasField("failure"):
            assert event.failure.code
            assert not event.failure.retryable
            return
        if event.HasField("result"):
            pytest.fail("Expected failure but got a successful result")
    pytest.fail("Execute stream closed without a failure event")
