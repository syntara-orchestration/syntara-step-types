"""Integration tests for the script step container via gRPC."""

from __future__ import annotations

import concurrent.futures
import json
import time

import grpc
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
                f"Step returned failure — code={event.failure.code} message={event.failure.message}"
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


def test_admission_control_busy_and_control_rpcs(
    script_stub: container_pb2_grpc.ContainerServiceStub,
) -> None:
    """Regression: 8 concurrent executions exhaust permits; 9th returns busy immediately;
    Health and Cancel remain available under full load (Aaron review finding)."""
    _HOLD_SECONDS = 30
    _PERMIT_COUNT = 8
    _CONTROL_DEADLINE = 0.5  # seconds — Control RPCs must respond within this

    hold_ids = [f"admission-hold-{i:03d}" for i in range(_PERMIT_COUNT)]
    inputs = json.dumps(
        {"language": "python", "code": f"import time; time.sleep({_HOLD_SECONDS})"}
    ).encode()

    def _hold_open(inv_id: str) -> str:
        req = container_pb2.ExecuteRequest(
            invocation_id=inv_id,
            workflow_run_id="wf-admission",
            step_revision_id=_STEP_REVISION_ID,
            runtime_abi=_RUNTIME_ABI,
            inputs_json=inputs,
            runtime_context_json=b"{}",
            credential_file_paths_json=b"{}",
            timeout_seconds=_HOLD_SECONDS + 5,
        )
        try:
            for event in script_stub.Execute(req, timeout=_HOLD_SECONDS + 5):
                if event.HasField("failure"):
                    return event.failure.code
        except grpc.RpcError:
            pass
        return "done"

    with concurrent.futures.ThreadPoolExecutor(max_workers=_PERMIT_COUNT) as pool:
        futures = [pool.submit(_hold_open, inv_id) for inv_id in hold_ids]

        # Allow time for all 8 to be admitted and running inside the container
        time.sleep(3)

        # --- Excess Execute must be rejected immediately as busy ---
        busy_req = container_pb2.ExecuteRequest(
            invocation_id="admission-excess-001",
            workflow_run_id="wf-admission",
            step_revision_id=_STEP_REVISION_ID,
            runtime_abi=_RUNTIME_ABI,
            inputs_json=inputs,
            runtime_context_json=b"{}",
            credential_file_paths_json=b"{}",
            timeout_seconds=30,
        )
        busy_start = time.monotonic()
        busy_seen = False
        for event in script_stub.Execute(busy_req, timeout=30):
            if event.HasField("failure"):
                elapsed = time.monotonic() - busy_start
                assert event.failure.retryable, "Busy rejection must be retryable"
                assert elapsed < _CONTROL_DEADLINE, (
                    f"Busy rejection took {elapsed:.3f}s — dispatch thread was blocked"
                )
                busy_seen = True
                break
        assert busy_seen, "Excess Execute did not return a failure event"

        # --- Health must respond promptly under full load ---
        health_start = time.monotonic()
        health = script_stub.Health(container_pb2.HealthRequest(), timeout=_CONTROL_DEADLINE)
        health_elapsed = time.monotonic() - health_start
        assert health.ready, "Health returned not-ready under load"
        assert health_elapsed < _CONTROL_DEADLINE, (
            f"Health took {health_elapsed:.3f}s under full load — dispatch thread was blocked"
        )

        # --- Cancel must be accepted promptly for an in-flight invocation ---
        cancel_start = time.monotonic()
        script_stub.Cancel(
            container_pb2.CancelRequest(invocation_id=hold_ids[0]),
            timeout=_CONTROL_DEADLINE,
        )
        cancel_elapsed = time.monotonic() - cancel_start
        assert cancel_elapsed < _CONTROL_DEADLINE, (
            f"Cancel took {cancel_elapsed:.3f}s — dispatch thread was blocked"
        )

        # Clean up remaining in-flight invocations
        for inv_id in hold_ids[1:]:
            try:
                script_stub.Cancel(
                    container_pb2.CancelRequest(invocation_id=inv_id),
                    timeout=1.0,
                )
            except grpc.RpcError:
                pass

        # Wait for all threads to finish
        concurrent.futures.wait(futures, timeout=10)
