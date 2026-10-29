"""Tests for the HTTP request step."""

from __future__ import annotations

import socket
from unittest.mock import patch

import httpx
import pytest
import respx
from syntara_node_http_request import HttpInput, HttpMethod, HttpRequestStep
from syntara_plugin.runtime import ContainerInvocationError, ExecutionContext, FailureCode


@pytest.fixture
def step() -> HttpRequestStep:
    return HttpRequestStep()


@pytest.fixture
def context() -> ExecutionContext:
    return ExecutionContext(invocation_id="test-invocation-1")


@pytest.fixture
def mock_dns():
    """Mock DNS resolution to return safe public IPs for all hostnames.

    This prevents tests from making real DNS queries and ensures deterministic results.
    """

    def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # Return a safe public IP for all test hostnames
        # Format: (family, type, proto, canonname, sockaddr)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", port or 80),
            ),  # example.com IP
        ]

    with patch("socket.getaddrinfo", side_effect=_getaddrinfo):
        yield


@respx.mock
def test_successful_get(step: HttpRequestStep, context: ExecutionContext, mock_dns) -> None:
    # DNS pinning replaces the hostname with the pinned IP before sending, so only
    # the IP-keyed mock is ever matched by respx.
    respx.get("https://93.184.216.34/api").mock(return_value=httpx.Response(200, json={"ok": True}))
    output = step.run(HttpInput(url="https://example.com/api"), context)
    assert output.status_code == 200
    assert output.body == {"ok": True}
    assert output.elapsed >= 0


@respx.mock
def test_post_with_json_body(step: HttpRequestStep, context: ExecutionContext, mock_dns) -> None:
    respx.post("https://93.184.216.34/api").mock(
        return_value=httpx.Response(201, json={"created": True})
    )
    output = step.run(
        HttpInput(url="https://example.com/api", method=HttpMethod.POST, body={"key": "value"}),
        context,
    )
    assert output.status_code == 201
    assert output.body == {"created": True}


@respx.mock
def test_query_params_merged(step: HttpRequestStep, context: ExecutionContext, mock_dns) -> None:
    respx.get("https://93.184.216.34/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    output = step.run(
        HttpInput(url="https://example.com/search", query_params={"q": "hello"}),
        context,
    )
    assert output.status_code == 200


@respx.mock
def test_text_response_body(step: HttpRequestStep, context: ExecutionContext, mock_dns) -> None:
    respx.get("https://93.184.216.34/text").mock(
        return_value=httpx.Response(200, text="hello world")
    )
    output = step.run(HttpInput(url="https://example.com/text"), context)
    assert output.body == "hello world"


@respx.mock
def test_404_raises_non_retryable(
    step: HttpRequestStep, context: ExecutionContext, mock_dns
) -> None:
    respx.get("https://93.184.216.34/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="https://example.com/missing"), context)
    assert exc_info.value.code == FailureCode.REMOTE_REJECTED
    assert not exc_info.value.retryable


@respx.mock
def test_503_raises_retryable(step: HttpRequestStep, context: ExecutionContext, mock_dns) -> None:
    respx.get("https://93.184.216.34/flaky").mock(return_value=httpx.Response(503))
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(HttpInput(url="https://example.com/flaky"), context)
    assert exc_info.value.code == FailureCode.UPSTREAM_TRANSIENT
    assert exc_info.value.retryable


@respx.mock
def test_429_raises_retryable(step: HttpRequestStep, context: ExecutionContext, mock_dns) -> None:
    respx.get("https://93.184.216.34/limited").mock(return_value=httpx.Response(429))
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


@respx.mock
def test_redirect_cookies_are_scoped_to_logical_hosts(step, context, mock_dns):
    """Two hostnames sharing an IP must not share host-only response cookies."""
    seen = []

    def respond(request):
        seen.append((request.headers["host"], request.headers.get("cookie")))
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={
                    "location": "https://other.example/middle",
                    "set-cookie": "session=private; Path=/; Secure; HttpOnly",
                },
            )
        if request.url.path == "/middle":
            return httpx.Response(302, headers={"location": "/finish"})
        return httpx.Response(200)

    respx.route().mock(side_effect=respond)
    step.run(HttpInput(url="https://example.com/start", follow_redirects=True), context)
    assert seen == [("example.com", None), ("other.example", None), ("other.example", None)]


@pytest.mark.parametrize(
    ("location", "same_origin"),
    [
        ("//other.example/finish", False),
        ("HTTP://example.com/finish", False),
        ("//example.com:8443/finish", False),
        ("https://example.com:443/finish", True),
        ("/finish", True),
    ],
)
@respx.mock
def test_redirect_credentials(step, context, mock_dns, location, same_origin):
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/middle"})
        if request.url.path == "/middle":
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200)

    respx.route().mock(side_effect=respond)
    step.run(
        HttpInput(
            url="https://example.com/start",
            follow_redirects=True,
            headers={"Authorization": "Bearer private", "Cookie": "explicit=private"},
        ),
        context,
    )
    assert seen[-1].headers.get("authorization") == ("Bearer private" if same_origin else None)
    assert seen[-1].headers.get("cookie") == ("explicit=private" if same_origin else None)


@respx.mock
def test_same_origin_redirect_uses_response_cookies(step, context, mock_dns):
    respx.get("https://93.184.216.34/start").mock(
        return_value=httpx.Response(
            302, headers={"location": "/finish", "set-cookie": "session=fresh; Path=/"}
        )
    )
    target = respx.get("https://93.184.216.34/finish").mock(return_value=httpx.Response(200))
    step.run(HttpInput(url="https://example.com/start", follow_redirects=True), context)
    assert target.calls[0].request.headers["cookie"] == "session=fresh"


@pytest.mark.parametrize("status", [307, 308])
@respx.mock
def test_redirect_preserves_post_body(step, context, mock_dns, status):
    respx.post("https://93.184.216.34/start").mock(
        return_value=httpx.Response(status, headers={"location": "/finish"})
    )
    target = respx.post("https://93.184.216.34/finish").mock(return_value=httpx.Response(200))
    step.run(
        HttpInput(
            url="https://example.com/start",
            method=HttpMethod.POST,
            body={"value": 1},
            follow_redirects=True,
        ),
        context,
    )
    assert target.calls[0].request.content == b'{"value":1}'


@respx.mock
def test_private_redirect_is_blocked(step, context, mock_dns):
    respx.get("https://93.184.216.34/start").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    )
    with pytest.raises(ContainerInvocationError) as exc:
        step.run(HttpInput(url="https://example.com/start", follow_redirects=True), context)
    assert exc.value.code == FailureCode.POLICY_DENIED
    assert len(respx.calls) == 1


def test_cancelled_http_never_starts(step, context):
    context.cancellation.set()
    with patch("socket.getaddrinfo") as resolve:
        with pytest.raises(ContainerInvocationError) as exc:
            step.run(HttpInput(url="https://example.com"), context)
    assert exc.value.code == FailureCode.CANCELLED
    assert not exc.value.retryable
    resolve.assert_not_called()


@respx.mock
def test_cancellation_interrupts_body_and_releases_capacity(step, context, mock_dns):
    import asyncio
    import time

    class WaitingBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            context.cancellation.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    body = WaitingBody()
    respx.get("https://93.184.216.34/slow").mock(return_value=httpx.Response(200, stream=body))
    start = time.monotonic()
    with pytest.raises(ContainerInvocationError) as exc:
        step.run(HttpInput(url="https://example.com/slow", timeout_seconds=30), context)
    assert exc.value.code == FailureCode.CANCELLED
    assert time.monotonic() - start < 1
    assert body.closed

    # All permits must be available after cancelled requests unwind.
    from syntara_node_http_request import _EXECUTE_SEMAPHORE

    acquired = []
    try:
        for _ in range(8):
            assert _EXECUTE_SEMAPHORE.acquire(blocking=False)
            acquired.append(True)
    finally:
        for _ in acquired:
            _EXECUTE_SEMAPHORE.release()


@respx.mock
def test_cancellation_interrupts_waiting_for_headers(step, context, mock_dns):
    import asyncio

    interrupted = []

    async def respond(request):
        context.cancellation.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.append(True)

    respx.get("https://93.184.216.34/slow").mock(side_effect=respond)
    with pytest.raises(ContainerInvocationError) as exc:
        step.run(HttpInput(url="https://example.com/slow"), context)
    assert exc.value.code == FailureCode.CANCELLED
    assert interrupted == [True]


@respx.mock
def test_response_cookie_path_is_respected(step, context, mock_dns):
    respx.get("https://93.184.216.34/start").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "/private/middle", "set-cookie": "session=private; Path=/private"},
        )
    )
    middle = respx.get("https://93.184.216.34/private/middle").mock(
        return_value=httpx.Response(302, headers={"location": "/public"})
    )
    finish = respx.get("https://93.184.216.34/public").mock(return_value=httpx.Response(200))
    step.run(HttpInput(url="https://example.com/start", follow_redirects=True), context)
    assert middle.calls[0].request.headers["cookie"] == "session=private"
    assert "cookie" not in finish.calls[0].request.headers


@pytest.mark.parametrize(
    ("url", "address", "authority"),
    [
        ("https://example.com:8443/path", "93.184.216.34", "example.com:8443"),
        ("https://xn--bcher-kva.example/path", "93.184.216.34", "xn--bcher-kva.example"),
        ("https://example.com/path", "2606:4700:4700::1111", "example.com"),
    ],
)
@respx.mock
def test_pinning_preserves_authority_and_sni(step, context, url, address, authority):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200)

    respx.route().mock(side_effect=respond)
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 443),
            )
        ],
    ) as resolve:
        step.run(HttpInput(url=url), context)
    resolve.assert_called_once()
    assert seen[0].url.host == address
    assert seen[0].headers["host"] == authority
    assert seen[0].extensions["sni_hostname"] == authority.split(":")[0]
