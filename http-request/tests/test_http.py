"""Tests for the HTTP request step."""

from __future__ import annotations

import pytest
import httpx
import respx
from syntara_plugin.runtime import ContainerInvocationError, ExecutionContext, FailureCode

from syntara_node_http_request import HttpInput, HttpMethod, HttpRequestStep


@pytest.fixture
def step() -> HttpRequestStep:
    return HttpRequestStep()


@pytest.fixture
def context() -> ExecutionContext:
    return ExecutionContext(invocation_id="test-invocation-1")


@respx.mock
def test_successful_get(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.get("https://example.com/api").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    output = step.run(HttpInput(url="https://example.com/api"), context)
    assert output.status_code == 200
    assert output.body == {"ok": True}
    assert output.elapsed >= 0


@respx.mock
def test_post_with_json_body(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.post("https://example.com/api").mock(
        return_value=httpx.Response(201, json={"created": True})
    )
    output = step.run(
        HttpInput(url="https://example.com/api", method=HttpMethod.POST, body={"key": "value"}),
        context,
    )
    assert output.status_code == 201
    assert output.body == {"created": True}


@respx.mock
def test_query_params_merged(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.get("https://example.com/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    output = step.run(
        HttpInput(url="https://example.com/search", query_params={"q": "hello"}),
        context,
    )
    assert output.status_code == 200


@respx.mock
def test_text_response_body(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.get("https://example.com/text").mock(
        return_value=httpx.Response(200, text="hello world")
    )
    output = step.run(HttpInput(url="https://example.com/text"), context)
    assert output.body == "hello world"


@respx.mock
def test_404_raises_non_retryable(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="https://example.com/missing"), context)
    assert exc_info.value.code == FailureCode.REMOTE_REJECTED
    assert not exc_info.value.retryable


@respx.mock
def test_503_raises_retryable(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.get("https://example.com/flaky").mock(return_value=httpx.Response(503))
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="https://example.com/flaky"), context)
    assert exc_info.value.code == FailureCode.UPSTREAM_TRANSIENT
    assert exc_info.value.retryable


@respx.mock
def test_429_raises_retryable(step: HttpRequestStep, context: ExecutionContext) -> None:
    respx.get("https://example.com/limited").mock(return_value=httpx.Response(429))
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="https://example.com/limited"), context)
    assert exc_info.value.code == FailureCode.UPSTREAM_TRANSIENT
    assert exc_info.value.retryable


def test_ssrf_private_ip_rejected(step: HttpRequestStep, context: ExecutionContext) -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="http://192.168.1.1/secret"), context)
    assert exc_info.value.code == FailureCode.POLICY_DENIED


def test_ssrf_loopback_rejected(step: HttpRequestStep, context: ExecutionContext) -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="http://127.0.0.1/secret"), context)
    assert exc_info.value.code == FailureCode.POLICY_DENIED


def test_ssrf_link_local_rejected(step: HttpRequestStep, context: ExecutionContext) -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="http://169.254.169.254/metadata"), context)
    assert exc_info.value.code == FailureCode.POLICY_DENIED


def test_non_http_scheme_rejected(step: HttpRequestStep, context: ExecutionContext) -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="ftp://example.com/file"), context)
    assert exc_info.value.code == FailureCode.POLICY_DENIED
