"""Integration tests for the script step container via gRPC."""

from __future__ import annotations

import concurrent.futures
import json
import time

import grpc
import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.helpers import execute_events, execute_output

pytestmark = pytest.mark.integration

# Wrapper keys stay on the SDK ContainerResult envelope; they must never leak
# into the business output object AO projects into ${node_id.*} namespaces.
_WRAPPER_KEYS = ("statusCode", "statusMessage", "errorResult")


def _assert_business_output(result: dict) -> None:
    """Assert the frozen script output fields (compatibility-matrix output freeze)."""
    assert isinstance(result["return_code"], int)
    assert isinstance(result["stdout"], str)
    assert isinstance(result["stderr"], str)
    for key in _WRAPPER_KEYS:
        assert key not in result, f"wrapper key {key!r} leaked into business output"


_PYTHON_STEP_REVISION_ID = "syntara.script-python.script-python"
_BASH_STEP_REVISION_ID = "syntara.script-bash.script-bash"
_RUNTIME_ABI = "syntara.container/v1alpha1"


def _execute(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    step_revision_id: str,
    code: str,
    environment: dict[str, str] | None = None,
    invocation_id: str = "test-001",
) -> dict:
    request = container_pb2.ExecuteRequest(
        invocation_id=invocation_id,
        workflow_run_id="test-wf-001",
        step_revision_id=step_revision_id,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps({"code": code, "environment": environment or {}}).encode(),
        runtime_context_json=b"{}",
        timeout_seconds=30,
    )
    return execute_output(stub, request, timeout=35)


def test_python_health_check(python_script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    response = python_script_stub.Health(container_pb2.HealthRequest())
    assert response.ready
    assert _PYTHON_STEP_REVISION_ID in list(response.step_revision_ids)


def test_bash_health_check(bash_script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    response = bash_script_stub.Health(container_pb2.HealthRequest())
    assert response.ready
    assert _BASH_STEP_REVISION_ID in list(response.step_revision_ids)


def test_python_print(python_script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        python_script_stub,
        step_revision_id=_PYTHON_STEP_REVISION_ID,
        code='print("hello from python")',
    )
    assert result["return_code"] == 0
    assert "hello from python" in result["stdout"]
    _assert_business_output(result)


def test_bash_echo(bash_script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        bash_script_stub,
        step_revision_id=_BASH_STEP_REVISION_ID,
        code='echo "hello from bash"',
    )
    assert result["return_code"] == 0
    assert "hello from bash" in result["stdout"]
    assert "stdout_json" not in result
    _assert_business_output(result)


def test_environment_variables(python_script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        python_script_stub,
        step_revision_id=_PYTHON_STEP_REVISION_ID,
        code="import os; print(os.environ['MY_VAR'])",
        environment={"MY_VAR": "hello-env"},
    )
    assert result["return_code"] == 0
    assert "hello-env" in result["stdout"]


def test_bash_environment_variables(
    bash_script_stub: container_pb2_grpc.ContainerServiceStub,
) -> None:
    result = _execute(
        bash_script_stub,
        step_revision_id=_BASH_STEP_REVISION_ID,
        code='printf "%s" "$MY_VAR"',
        environment={"MY_VAR": "hello-env"},
    )
    assert result["return_code"] == 0
    assert result["stdout"] == "hello-env"


def test_json_output_parsing(python_script_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        python_script_stub,
        step_revision_id=_PYTHON_STEP_REVISION_ID,
        code='import json; print(json.dumps({"status": "ok", "value": 42}))',
    )
    assert result["return_code"] == 0
    assert result["stdout_json"] == {"status": "ok", "value": 42}


def test_script_failure_handling(
    python_script_stub: container_pb2_grpc.ContainerServiceStub,
) -> None:
    request = container_pb2.ExecuteRequest(
        invocation_id="test-fail-001",
        workflow_run_id="test-wf-001",
        step_revision_id=_PYTHON_STEP_REVISION_ID,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps({"code": "raise SystemExit(1)", "environment": {}}).encode(),
        runtime_context_json=b"{}",
        timeout_seconds=30,
    )
    for event in execute_events(python_script_stub, request, timeout=35):
        if event.HasField("result"):
            assert event.result.status_code != 0
            error = json.loads(event.result.error_result_json)
            assert error["code"] == "REMOTE_REJECTED"
            assert error["category"] == "business"
            assert not error["retryable"]
            return
        if event.HasField("failure"):
            pytest.fail(f"Expected an error result, got runtime failure: {event.failure.code}")
    pytest.fail("Execute stream closed without an error result")


def _assert_admission_control_busy_and_control_rpcs(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    step_revision_id: str,
    script_code: str,
    invocation_prefix: str,
) -> None:
    """Verify a plugin container enforces its limit and keeps control RPCs available."""
    _HOLD_SECONDS = 30
    _PERMIT_COUNT = 8
    _CONTROL_DEADLINE = 0.5  # seconds — Control RPCs must respond within this

    hold_ids = [f"{invocation_prefix}-admission-hold-{i:03d}" for i in range(_PERMIT_COUNT)]
    inputs = json.dumps({"code": script_code}).encode()

    def _hold_open(inv_id: str) -> str:
        req = container_pb2.ExecuteRequest(
            invocation_id=inv_id,
            workflow_run_id=f"wf-admission-{invocation_prefix}",
            step_revision_id=step_revision_id,
            runtime_abi=_RUNTIME_ABI,
            inputs_json=inputs,
            runtime_context_json=b"{}",
            timeout_seconds=_HOLD_SECONDS + 5,
        )
        try:
            for event in execute_events(stub, req, timeout=_HOLD_SECONDS + 5):
                if event.HasField("result") and event.result.status_code:
                    return json.loads(event.result.error_result_json)["code"]
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
            invocation_id=f"{invocation_prefix}-admission-excess-001",
            workflow_run_id=f"wf-admission-{invocation_prefix}",
            step_revision_id=step_revision_id,
            runtime_abi=_RUNTIME_ABI,
            inputs_json=inputs,
            runtime_context_json=b"{}",
            timeout_seconds=30,
        )
        busy_start = time.monotonic()
        busy_seen = False
        for event in execute_events(stub, busy_req, timeout=30):
            if event.HasField("result"):
                assert event.result.status_code != 0, "Busy request unexpectedly succeeded"
                error = json.loads(event.result.error_result_json)
                elapsed = time.monotonic() - busy_start
                assert error["code"] == "UPSTREAM_TRANSIENT"
                assert error["category"] == "dependency"
                assert error["retryable"], "Busy rejection must be retryable"
                assert elapsed < _CONTROL_DEADLINE, (
                    f"Busy rejection took {elapsed:.3f}s — dispatch thread was blocked"
                )
                busy_seen = True
                break
        assert busy_seen, "Excess Execute did not return an error result"

        # --- Health must respond promptly under full load ---
        health_start = time.monotonic()
        health = stub.Health(container_pb2.HealthRequest(), timeout=_CONTROL_DEADLINE)
        health_elapsed = time.monotonic() - health_start
        assert health.ready, "Health returned not-ready under load"
        assert health_elapsed < _CONTROL_DEADLINE, (
            f"Health took {health_elapsed:.3f}s under full load — dispatch thread was blocked"
        )

        # --- Cancel must be accepted promptly for an in-flight invocation ---
        cancel_start = time.monotonic()
        stub.Cancel(
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
                stub.Cancel(
                    container_pb2.CancelRequest(invocation_id=inv_id),
                    timeout=1.0,
                )
            except grpc.RpcError:
                pass

        # Wait for all threads to finish
        concurrent.futures.wait(futures, timeout=10)


def test_python_admission_control_busy_and_control_rpcs(
    python_script_stub: container_pb2_grpc.ContainerServiceStub,
) -> None:
    _assert_admission_control_busy_and_control_rpcs(
        python_script_stub,
        step_revision_id=_PYTHON_STEP_REVISION_ID,
        script_code="import time; time.sleep(30)",
        invocation_prefix="python",
    )


def test_bash_admission_control_busy_and_control_rpcs(
    bash_script_stub: container_pb2_grpc.ContainerServiceStub,
) -> None:
    _assert_admission_control_busy_and_control_rpcs(
        bash_script_stub,
        step_revision_id=_BASH_STEP_REVISION_ID,
        script_code="sleep 30",
        invocation_prefix="bash",
    )
