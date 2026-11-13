"""Verify HTTP errors from the built image are serialized as gRPC Result events."""

from __future__ import annotations

import json

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.helpers import execute_events

pytestmark = pytest.mark.integration

_STEP_REVISION_ID = "syntara.http-request.http-request"


@pytest.mark.parametrize(
    ("path", "http_timeout", "expected_code", "expected_category"),
    [
        ("timeout", 1, "TIMEOUT", "timeout"),
        ("unavailable", 10, "UPSTREAM_TRANSIENT", "dependency"),
    ],
)
def test_http_errors_are_serialized_in_result_event(
    http_stub: container_pb2_grpc.ContainerServiceStub,
    test_upstream: str,
    path: str,
    http_timeout: int,
    expected_code: str,
    expected_category: str,
) -> None:
    request = container_pb2.ExecuteRequest(
        invocation_id=f"http-error-{path}",
        workflow_run_id="wf-http-error",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi="syntara.container/v1alpha1",
        inputs_json=json.dumps(
            {"url": f"{test_upstream}/{path}", "timeout_seconds": http_timeout}
        ).encode(),
        runtime_context_json=b"{}",
        timeout_seconds=12,
    )
    events = list(execute_events(http_stub, request, timeout=15))
    results = [event.result for event in events if event.HasField("result")]
    failures = [event.failure for event in events if event.HasField("failure")]

    assert not failures, f"Expected an error Result event, got {failures}"
    assert len(results) == 1
    result = results[0]
    assert result.status_code != 0
    error = json.loads(result.error_result_json)
    assert error["code"] == expected_code
    assert error["category"] == expected_category
    assert error["retryable"] is True
