"""Integration tests for the AAP Workflow step container via gRPC.

Exercises the built image against a controlled fake AAP gateway: workflow
inputs and credentials, parent identity/status, successful and failed parents,
launch rejection, missing template, cancellation with a best-effort AAP cancel,
timeout, uncertain-launch adoption, and startup/manifest agreement.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
import uuid
from collections.abc import Iterator
from queue import Queue
from types import SimpleNamespace

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.service_types import AAP_TEST_TOKEN, ImageServices

pytestmark = pytest.mark.integration

_STEP_REVISION_ID = "syntara.aap.aap-workflow"
_RUNTIME_ABI = "syntara.container/v1alpha1"
_CREDENTIAL = "syntara.aap.credential"
_PREFIX = "/api/controller/v2"


def _upstream_state(image_services: ImageServices) -> dict:
    """Read the fake gateway's plain-HTTP debug state (no auth required)."""
    import httpx

    return httpx.get(image_services.aap_state_url, timeout=5).json()


def _execute_aap(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    inputs: dict,
    aap_environment: dict,
    invocation_id: str | None = None,
    timeout_seconds: int = 30,
    credential_document: dict | None = None,
) -> tuple[container_pb2.Result | None, container_pb2.Failure | None]:
    """Run one Execute with the AAP credential callback, returning (result, failure)."""
    invocation_id = invocation_id or f"aap-test-{uuid.uuid4().hex[:8]}"
    request = container_pb2.ExecuteRequest(
        invocation_id=invocation_id,
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps(inputs).encode(),
        runtime_context_json=json.dumps(aap_environment).encode(),
        timeout_seconds=timeout_seconds,
    )
    outbound: Queue[container_pb2.ExecuteClientMessage | None] = Queue()
    outbound.put(container_pb2.ExecuteClientMessage(start=request))
    if credential_document is None:
        # The step always requires the declared credential; supply the token.
        credential_document = {"token": AAP_TEST_TOKEN}

    def requests() -> Iterator[container_pb2.ExecuteClientMessage]:
        while True:
            message = outbound.get()
            if message is None:
                return
            yield message

    call = stub.Execute(requests(), timeout=timeout_seconds + 10)
    try:
        for message in call:
            if message.HasField("credential_request"):
                credential_request = message.credential_request
                assert credential_request.name == _CREDENTIAL
                outbound.put(
                    container_pb2.ExecuteClientMessage(
                        credential_response=container_pb2.CredentialResponse(
                            invocation_id=credential_request.invocation_id,
                            request_id=credential_request.request_id,
                            document_json=json.dumps(credential_document).encode(),
                        )
                    )
                )
                continue
            event = message.event
            if event.HasField("result"):
                if event.result.status_code:
                    # A deliberate StepFailure arrives as a Result with a
                    # nonzero status code and an error_result document.
                    error = json.loads(event.result.error_result_json.decode())
                    return None, SimpleNamespace(
                        code=error.get("code", ""),
                        message=error.get("message", ""),
                    )
                return event.result, None
            if event.HasField("failure"):
                return None, event.failure
        pytest.fail("Execute stream closed without a result or failure event")
    finally:
        outbound.put(None)
        call.cancel()


def _run(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    inputs: dict,
    aap_environment: dict,
    invocation_id: str | None = None,
    timeout_seconds: int = 30,
    credential_document: dict | None = None,
) -> dict:
    result, failure = _execute_aap(
        stub,
        inputs=inputs,
        aap_environment=aap_environment,
        invocation_id=invocation_id,
        timeout_seconds=timeout_seconds,
        credential_document=credential_document,
    )
    if failure is not None:
        pytest.fail(f"Step returned failure — {failure.code}: {failure.message}")
    assert result is not None
    assert not result.status_code, result.error_result_json.decode()
    return json.loads(result.output_json)


def _run_failure(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    inputs: dict,
    aap_environment: dict,
    invocation_id: str | None = None,
    timeout_seconds: int = 30,
    credential_document: dict | None = None,
):
    result, failure = _execute_aap(
        stub,
        inputs=inputs,
        aap_environment=aap_environment,
        invocation_id=invocation_id,
        timeout_seconds=timeout_seconds,
        credential_document=credential_document,
    )
    if failure is not None:
        return failure
    assert result is not None
    pytest.fail(f"Step returned a result instead of a failure: {result.output_json.decode()}")


def test_successful_workflow(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    output = _run(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 42, "extra_vars": {"env": "prod"}},
        aap_environment=aap_environment,
    )
    assert output["status"] == "completed"
    assert output["workflow_job_id"] == 7
    assert output["workflow_job_status"] == "successful"
    assert output["workflow_job_url"].endswith("/execution/jobs/workflow/7/output")
    assert output["artifacts"] == {"summary": "ok"}
    assert output["created"] == "2026-01-01T00:00:00Z"
    assert set(output) == {
        "status",
        "workflow_job_id",
        "workflow_job_url",
        "workflow_job_status",
        "artifacts",
        "created",
        "started",
        "finished",
    }  # declared manifest output properties — startup/schema agreement


def test_failed_parent_returns_failed_branch(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    output = _run(
        aap_workflow_stub, inputs={"workflow_job_template_id": 43}, aap_environment=aap_environment
    )
    assert output["status"] == "failed"
    assert output["workflow_job_id"] == 8
    assert output["workflow_job_status"] == "failed"
    assert output["error"]


def test_template_name_lookup(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    output = _run(
        aap_workflow_stub,
        inputs={
            "workflow_job_template_name": "example-workflow",
            "organization_name": "Default",
        },
        aap_environment=aap_environment,
    )
    assert output["status"] == "completed"
    assert output["workflow_job_id"] == 7


def test_missing_template(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    failure = _run_failure(
        aap_workflow_stub,
        inputs={
            "workflow_job_template_name": "does-not-exist",
            "organization_name": "Default",
        },
        aap_environment=aap_environment,
    )
    assert failure.code == "NOT_FOUND"


def test_launch_authentication_failure(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    failure = _run_failure(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 44},
        aap_environment=aap_environment,
        credential_document={"token": "wrong-token"},  # gateway rejects it
    )
    assert failure.code == "AUTHENTICATION_FAILED"


def test_launch_missing_template_id(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    failure = _run_failure(
        aap_workflow_stub, inputs={"workflow_job_template_id": 45}, aap_environment=aap_environment
    )
    assert failure.code == "NOT_FOUND"


def test_missing_endpoint_fails_before_credential(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    failure = _run_failure(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 42},
        aap_environment={},  # no aapEndpoint
    )
    assert failure.code == "POLICY_DENIED"


def test_bad_credential_fails(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    failure = _run_failure(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 42},
        aap_environment=aap_environment,
        credential_document={},  # no auth fields
    )
    assert failure.code == "POLICY_DENIED"


def test_uncertain_launch_adopts_correlated_job(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub,
    aap_environment: dict,
    image_services: ImageServices,
) -> None:
    """Template 48 drops the launch response; the executor must adopt, not re-POST."""
    output = _run(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 48},
        aap_environment=aap_environment,
        timeout_seconds=25,
    )
    assert output["status"] == "completed"
    state = _upstream_state(image_services)
    assert state["launchCounts"].get("48", 0) == 1  # exactly one launch POST


def test_cancellation_requests_aap_cancel(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub,
    aap_environment: dict,
    image_services: ImageServices,
) -> None:
    """Template 47 runs forever; Cancel must request the AAP cancel and report CANCELLED."""
    invocation_id = f"aap-cancel-{uuid.uuid4().hex[:8]}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as caller:
        outcome = caller.submit(
            lambda: _execute_aap(
                aap_workflow_stub,
                inputs={"workflow_job_template_id": 47},
                aap_environment=aap_environment,
                invocation_id=invocation_id,
                timeout_seconds=30,
            )
        )
        # Wait until the gateway has seen the launch.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _upstream_state(image_services)["launchCounts"].get("47"):
                break
            time.sleep(0.2)
        else:
            pytest.fail("Fake AAP gateway never received the launch")

        response = aap_workflow_stub.Cancel(
            container_pb2.CancelRequest(invocation_id=invocation_id), timeout=2
        )
        assert response.accepted
        _result, failure = outcome.result(timeout=15)
        assert failure is not None
        assert failure.code == "CANCELLED"

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _upstream_state(image_services)["cancelRequests"]:
            break
        time.sleep(0.2)
    else:
        pytest.fail("Best-effort AAP cancel was never requested")
    # The gateway authenticated the cancel call with the step's token.
    assert _upstream_state(image_services)["authMatches"][-1]


def test_timeout_with_known_job(
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub, aap_environment: dict
) -> None:
    failure = _run_failure(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 47, "timeout": 2},
        aap_environment=aap_environment,
        timeout_seconds=15,
    )
    assert failure.code == "TIMEOUT"
    assert "9" in failure.message  # the known workflow job id is reported


def test_container_logs_do_not_leak_the_credential(
    image_services: ImageServices,
    aap_workflow_stub: container_pb2_grpc.ContainerServiceStub,
    aap_environment: dict,
) -> None:
    import subprocess

    _run(
        aap_workflow_stub,
        inputs={"workflow_job_template_id": 42, "extra_vars": {"secretish": "leaky"}},
        aap_environment=aap_environment,
    )
    logs = subprocess.run(
        ["podman", "logs", image_services.aap_container_name],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    combined = logs.stdout + logs.stderr
    assert AAP_TEST_TOKEN not in combined
    assert "leaky" not in combined
