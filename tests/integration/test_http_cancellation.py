"""Exercise HTTP cancellation through the built image and a controlled upstream."""

from __future__ import annotations

import concurrent.futures
import json
import time
import uuid

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.helpers import execute_events, execute_output

pytestmark = pytest.mark.integration

_STEP_REVISION_ID = "syntara.http-request.http-request"


def _upstream_state(
    stub: container_pb2_grpc.ContainerServiceStub, url: str, invocation_id: str
) -> dict:
    request = container_pb2.ExecuteRequest(
        invocation_id=invocation_id,
        workflow_run_id="wf-http-cancel",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi="syntara.container/v1alpha1",
        inputs_json=json.dumps({"url": f"{url}/state"}).encode(),
        runtime_context_json=b"{}",
        timeout_seconds=5,
    )
    return execute_output(stub, request, timeout=7)["body"]


def test_http_cancel_stops_io_and_keeps_control_rpcs_available(
    http_stub: container_pb2_grpc.ContainerServiceStub, test_upstream: str
) -> None:
    request = container_pb2.ExecuteRequest(
        invocation_id="http-cancel-regression",
        workflow_run_id="wf-cancel",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi="syntara.container/v1alpha1",
        inputs_json=json.dumps({"url": f"{test_upstream}/cancel"}).encode(),
        runtime_context_json=b"{}",
        timeout_seconds=10,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(lambda: list(execute_events(http_stub, request, timeout=12)))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = _upstream_state(
                http_stub, test_upstream, f"http-cancel-state-{uuid.uuid4().hex}"
            )
            if state["started"]:
                break
            time.sleep(0.1)
        else:
            pytest.fail("Controlled upstream never received the HTTP request")

        response = http_stub.Cancel(
            container_pb2.CancelRequest(invocation_id=request.invocation_id), timeout=1
        )
        assert response.accepted
        events = result.result(timeout=3)
        assert events[-1].failure.code == "CANCELLED"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = _upstream_state(
                http_stub, test_upstream, f"http-cancel-state-{uuid.uuid4().hex}"
            )
            if state["cancelled"]:
                break
            time.sleep(0.1)
        else:
            pytest.fail("HTTP I/O continued after cancellation")

        assert http_stub.Health(container_pb2.HealthRequest(), timeout=1).ready
