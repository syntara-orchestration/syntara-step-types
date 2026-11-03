"""Integration tests for the HTTP request step container via gRPC."""

from __future__ import annotations

import json

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.helpers import execute_output

pytestmark = pytest.mark.integration

_STEP_REVISION_ID = "syntara.http-request.http-request"
_RUNTIME_ABI = "syntara.container/v1alpha1"


def _execute(
    stub: container_pb2_grpc.ContainerServiceStub,
    *,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    query_params: dict[str, str] | None = None,
    body=None,
    invocation_id: str = "test-http-001",
) -> dict:
    inputs: dict = {"url": url, "method": method}
    if headers:
        inputs["headers"] = headers
    if query_params:
        inputs["query_params"] = query_params
    if body is not None:
        inputs["body"] = body

    request = container_pb2.ExecuteRequest(
        invocation_id=invocation_id,
        workflow_run_id="test-wf-001",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps(inputs).encode(),
        runtime_context_json=b"{}",
        credential_file_paths_json=b"{}",
        timeout_seconds=30,
    )
    return execute_output(stub, request, timeout=35)


def test_health_check(http_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    response = http_stub.Health(container_pb2.HealthRequest())
    assert response.ready
    assert _STEP_REVISION_ID in list(response.step_revision_ids)


def test_get_request(http_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(http_stub, url="https://httpbin.org/get")
    assert result["status_code"] == 200
    assert result["elapsed"] >= 0
    assert isinstance(result["body"], dict)


def test_post_with_json_body(http_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        http_stub,
        url="https://httpbin.org/post",
        method="POST",
        body={"key": "value"},
    )
    assert result["status_code"] == 200
    assert result["body"]["json"] == {"key": "value"}


def test_query_params(http_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    result = _execute(
        http_stub,
        url="https://httpbin.org/get",
        query_params={"foo": "bar"},
    )
    assert result["status_code"] == 200
    assert result["body"]["args"] == {"foo": "bar"}


def test_ssrf_private_ip_blocked(http_stub: container_pb2_grpc.ContainerServiceStub) -> None:
    request = container_pb2.ExecuteRequest(
        invocation_id="test-ssrf-001",
        workflow_run_id="test-wf-001",
        step_revision_id=_STEP_REVISION_ID,
        runtime_abi=_RUNTIME_ABI,
        inputs_json=json.dumps({"url": "http://192.168.1.1/secret"}).encode(),
        runtime_context_json=b"{}",
        credential_file_paths_json=b"{}",
        timeout_seconds=10,
    )
    for event in http_stub.Execute(request, timeout=15):
        if event.HasField("result"):
            assert event.result.status_code != 0
            error = json.loads(event.result.error_result_json)
            assert error["code"] == "POLICY_DENIED"
            assert error["category"] == "policy"
            assert not error["retryable"]
            return
        if event.HasField("failure"):
            pytest.fail(f"Expected an error result, got runtime failure: {event.failure.code}")
    pytest.fail("Execute stream closed without an error result")
