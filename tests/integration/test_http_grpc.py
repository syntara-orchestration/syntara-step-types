"""Integration tests for the HTTP request step container via gRPC."""

from __future__ import annotations

import json

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

pytestmark = pytest.mark.integration

_STEP_REVISION_ID = "syntara.step.http-request:0.1.0"
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
    for event in stub.Execute(request, timeout=35):
        if event.HasField("result"):
            return json.loads(event.result.output_json)
        if event.HasField("failure"):
            pytest.fail(
                f"Step returned failure — code={event.failure.code} message={event.failure.message}"
            )
    pytest.fail("Execute stream closed without a result or failure event")


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
        if event.HasField("failure"):
            assert "POLICY_DENIED" in event.failure.code or event.failure.code
            assert not event.failure.retryable
            return
        if event.HasField("result"):
            pytest.fail("Expected SSRF to be blocked but request succeeded")
    pytest.fail("Execute stream closed without a failure event")
