"""HTTP execution logic for the HTTP request step."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from http import HTTPStatus
from threading import Event
from typing import Any
from urllib.parse import parse_qsl, urlparse, urlsplit, urlunsplit

import httpx
from syntara_plugin.runtime import ContainerInvocationError, FailureCode

from ._models import HttpInput, HttpOutput

# SDK permits 1 MiB for entire serialized result. Limit response body to account for:
# - JSON encoding overhead (quotes, escaping)
# - Result envelope (status_code, headers, elapsed fields)
_MAX_RESPONSE_BYTES = 819_200  # 800 KB
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_MAX_REDIRECTS = 10


def _is_blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address should be blocked by SSRF policy."""
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


def _validate_url_no_ssrf(url: str) -> tuple[str, str | None]:
    """Reject URLs targeting private, link-local, loopback, or non-HTTP schemes.

    Validates both IP literals and resolves hostnames to check all addresses.
    Returns (validated_url, pinned_ip) where pinned_ip should be used for the connection.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            "URL must use http:// or https:// scheme.",
            retryable=False,
            outcome="not_started",
        )

    # Use httpx.URL.host as the map key — httpx decodes punycode ACE to unicode,
    # matching what request.url.host returns in _pin_request_dns (consistent lookup).
    # For socket.getaddrinfo use the ACE (punycode) form from the httpx string
    # representation, which is portable across glibc and musl resolvers.
    httpx_url = httpx.URL(url)
    host = httpx_url.host  # unicode, used as pinned_hosts map key
    ace_host = urlparse(str(httpx_url)).hostname or host  # ACE, used for getaddrinfo
    if not host:
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            "URL must specify a hostname.",
            retryable=False,
            outcome="not_started",
        )

    # Try parsing as IP address first
    try:
        addr = ipaddress.ip_address(host)
        if _is_blocked_address(addr):
            raise ContainerInvocationError(
                FailureCode.POLICY_DENIED,
                f"URL targets a blocked address: {host}",
                retryable=False,
                outcome="not_started",
            )
        return url, None  # IP literal, no need to pin
    except ValueError:
        pass  # Not an IP literal, continue to DNS resolution

    # Resolve hostname and check all resolved addresses.
    # Use the ACE (punycode) form for portability across glibc and musl resolvers.
    try:
        addr_info = socket.getaddrinfo(ace_host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        # EAI_AGAIN is the only genuinely transient resolver failure.
        # EAI_NONAME / EAI_FAIL indicate permanent name resolution failure.
        if exc.errno == socket.EAI_AGAIN:
            # Temporary resolver failure - retryable
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                f"Temporary DNS failure for hostname: {host}",
                retryable=True,
                outcome="not_completed",
            ) from exc
        # Other errors (e.g., invalid hostname) are permanent
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            f"Cannot resolve hostname: {host}",
            retryable=False,
            outcome="not_started",
        ) from exc

    if not addr_info:
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            f"No addresses resolved for hostname: {host}",
            retryable=False,
            outcome="not_started",
        )

    # Check all resolved addresses and pick first valid one to pin
    pinned_ip: str | None = None
    for _family, _, _, _, sockaddr in addr_info:
        ip_str = str(sockaddr[0])
        try:
            addr = ipaddress.ip_address(ip_str)
            if _is_blocked_address(addr):
                raise ContainerInvocationError(
                    FailureCode.POLICY_DENIED,
                    f"Hostname {host} resolves to blocked address: {ip_str}",
                    retryable=False,
                    outcome="not_started",
                )
            if pinned_ip is None:
                pinned_ip = ip_str  # Pin first valid address
        except ValueError:
            # Should not happen with getaddrinfo results, but be safe
            continue

    return url, pinned_ip


def _resolve_query_params(
    url: str, query_params: dict[str, str]
) -> tuple[str, dict[str, str] | None]:
    if not query_params:
        return url, None
    split = urlsplit(url)
    existing = dict(parse_qsl(split.query, keep_blank_values=True))
    merged = {**existing, **query_params}
    # Strip fragment — clients must not send the fragment component in HTTP requests (RFC 9110 §4.1).
    clean = urlunsplit((split.scheme, split.netloc, split.path, "", ""))
    return clean, merged


async def _stream_response_body_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Stream response body with size limit enforced during reading."""
    chunks: list[bytes] = []
    total_read = 0

    async for chunk in response.aiter_bytes(chunk_size=65536):
        chunk_size = len(chunk)
        if total_read + chunk_size > max_bytes:
            remaining = max_bytes - total_read
            if remaining > 0:
                chunks.append(chunk[:remaining])
            await response.aclose()
            raise ContainerInvocationError(
                FailureCode.POLICY_DENIED,
                f"Response body exceeds {max_bytes} byte limit.",
                retryable=False,
                outcome="not_completed",
            )
        chunks.append(chunk)
        total_read += chunk_size

    return b"".join(chunks)


async def _bound_response_body(response: httpx.Response) -> Any:
    """Parse response body as JSON or text, enforcing size limit during streaming."""
    content = await _stream_response_body_limited(response, _MAX_RESPONSE_BYTES)
    try:
        return json.loads(content)
    except Exception:  # noqa: BLE001
        return content.decode("utf-8", errors="replace")


def _pin_request_dns(request: httpx.Request, pinned_hosts: dict[str, str]) -> httpx.Request:
    """Modify a request to use pinned IP instead of hostname while preserving SNI.

    Prevents DNS rebinding attacks by forcing connections to validated IPs (PR #8 review fix).
    Handles URLs with userinfo (user:pass@host) and IPv6 addresses with proper bracketing.

    Args:
        request: The httpx Request to modify
        pinned_hosts: Map of hostname -> IP to pin

    Returns:
        Modified request with pinned IP
    """
    url = request.url
    hostname = url.host

    if not hostname or hostname not in pinned_hosts:
        return request

    pinned_ip = pinned_hosts[hostname]
    parsed = urlparse(str(url))

    # Handle IPv6 addresses with brackets (required for URLs with ports)
    if ":" in pinned_ip and not pinned_ip.startswith("["):
        ip_part = f"[{pinned_ip}]"
    else:
        ip_part = pinned_ip

    # Preserve userinfo (username:password@) if present
    if parsed.username:
        if parsed.password:
            userinfo = f"{parsed.username}:{parsed.password}@"
        else:
            userinfo = f"{parsed.username}@"
    else:
        userinfo = ""

    # Build new netloc: [userinfo@]IP[:port]
    if parsed.port:
        new_netloc = f"{userinfo}{ip_part}:{parsed.port}"
    else:
        new_netloc = f"{userinfo}{ip_part}"

    # Reconstruct URL with pinned IP using urlunsplit for reliability
    new_url_str = urlunsplit(
        (
            parsed.scheme,
            new_netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )

    # Preserve the stream, including HTTPX's streamed 307/308 redirect bodies.
    new_request = httpx.Request(
        method=request.method,
        url=new_url_str,
        headers=request.headers,
        stream=request.stream,
        extensions=dict(request.extensions),
    )

    # Set Host header to original hostname, preserving non-default ports.
    # HTTP/1.1 requires host:port when the port is non-standard.
    default_port = {"http": 80, "https": 443}.get(parsed.scheme, 0)
    if parsed.port and parsed.port != default_port:
        new_request.headers["host"] = f"{url.raw_host.decode('ascii')}:{parsed.port}"
    else:
        new_request.headers["host"] = url.raw_host.decode("ascii")

    # For HTTPS, set SNI server_hostname extension
    if parsed.scheme == "https":
        new_request.extensions["sni_hostname"] = url.raw_host.decode("ascii")

    return new_request


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    """Return (scheme, host, effective_port) for same-origin comparisons."""
    default_port = {"http": 80, "https": 443}.get(url.scheme, 0)
    return url.scheme, url.host, url.port or default_port


class _PinnedTransport(httpx.AsyncBaseTransport):
    """Pin connections without exposing IP URLs to HTTPX's cookies and redirects."""

    def __init__(self) -> None:
        # Separate pools prevent TLS connections being reused between logical
        # origins that happen to resolve to the same IP address.
        self._transports: dict[tuple[str, str, int], httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        _, pinned_ip = _validate_url_no_ssrf(str(request.url))
        pins = {request.url.host: pinned_ip} if pinned_ip else {}
        if not pins:
            # Only IP literals may be sent without a DNS pin.
            ipaddress.ip_address(request.url.host)
        origin = _origin(request.url)
        transport = self._transports.get(origin)
        if transport is None:
            transport = httpx.AsyncHTTPTransport(trust_env=False)
            self._transports[origin] = transport
        return await transport.handle_async_request(_pin_request_dns(request, pins))

    async def aclose(self) -> None:
        for transport in self._transports.values():
            await transport.aclose()


async def _make_request_with_validated_redirects(
    client: httpx.AsyncClient,
    request: httpx.Request,
    follow_redirects: bool,
) -> httpx.Response:
    """Follow logical HTTPX redirects; the transport validates and pins every hop."""
    explicit_cookie = request.headers.get("cookie")
    for redirect_count in range(_MAX_REDIRECTS + 1):
        response = await client.send(request, stream=True)
        next_request = response.next_request
        if not follow_redirects or next_request is None:
            return response
        await response.aclose()
        if redirect_count == _MAX_REDIRECTS:
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                f"Too many redirects (>{_MAX_REDIRECTS})",
                retryable=False,
                outcome="not_completed",
            )
        # Strip explicit credentials on any origin change, including HTTPS upgrades.
        if _origin(next_request.url) != _origin(request.url):
            explicit_cookie = None
            for name in ("authorization", "cookie"):
                next_request.headers.pop(name, None)
        elif explicit_cookie is not None and "cookie" not in next_request.headers:
            next_request.headers["cookie"] = explicit_cookie
        request = next_request
    raise AssertionError("unreachable redirect loop")


async def _wait_for_cancellation(cancellation: Event) -> None:
    while not cancellation.is_set():
        await asyncio.sleep(0.05)


async def _execute_cancellable(inputs: HttpInput, cancellation: Event) -> HttpOutput:
    execution = asyncio.create_task(_execute_http_request(inputs))
    cancelled = asyncio.create_task(_wait_for_cancellation(cancellation))
    try:
        await asyncio.wait((execution, cancelled), return_when=asyncio.FIRST_COMPLETED)
        if cancellation.is_set():
            raise ContainerInvocationError(
                FailureCode.CANCELLED,
                "HTTP request was cancelled.",
                retryable=False,
                outcome="not_completed",
            )
        return await execution
    finally:
        for task in (execution, cancelled):
            task.cancel()
        await asyncio.gather(execution, cancelled, return_exceptions=True)


def execute_http_request(inputs: HttpInput, cancellation: Event | None = None) -> HttpOutput:
    """Run the async HTTP operation from the synchronous SDK execution hook."""
    if cancellation is not None and cancellation.is_set():
        raise ContainerInvocationError(
            FailureCode.CANCELLED,
            "HTTP request was cancelled before starting.",
            retryable=False,
            outcome="not_started",
        )
    if cancellation is None:
        return asyncio.run(_execute_http_request(inputs))
    return asyncio.run(_execute_cancellable(inputs, cancellation))


async def _execute_http_request(inputs: HttpInput) -> HttpOutput:
    """Make a bounded, SSRF-safe HTTP request and return structured output."""
    url, params = _resolve_query_params(inputs.url, dict(inputs.query_params))
    headers = dict(inputs.headers)

    # Prepare request body
    json_body = inputs.body if isinstance(inputs.body, dict) else None
    str_body = inputs.body.encode() if isinstance(inputs.body, str) else None

    start = time.time()
    try:
        async with httpx.AsyncClient(transport=_PinnedTransport(), trust_env=False) as client:
            # Build initial request
            request = client.build_request(
                method=inputs.method.value,
                url=url,
                headers=headers,
                params=params,
                json=json_body,
                content=str_body,
                timeout=float(inputs.timeout_seconds),
            )

            # Execute with validated redirects and streaming
            response = await _make_request_with_validated_redirects(
                client=client,
                request=request,
                follow_redirects=inputs.follow_redirects,
            )

            # Read response body INSIDE the client context to avoid "Bad file descriptor"
            try:
                body = await _bound_response_body(response)
            finally:
                await response.aclose()
            elapsed = time.time() - start
            status_code = response.status_code
            response_headers = dict(response.headers)
            reason_phrase = response.reason_phrase

    except httpx.TimeoutException as exc:
        raise ContainerInvocationError(
            FailureCode.TIMEOUT,
            "HTTP request timed out.",
            retryable=True,
            outcome="not_completed",
        ) from exc
    except ContainerInvocationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ContainerInvocationError(
            FailureCode.UPSTREAM_TRANSIENT,
            f"HTTP request failed: {type(exc).__name__}",
            retryable=True,
            outcome="not_completed",
        ) from exc

    if status_code >= HTTPStatus.BAD_REQUEST:
        retryable = status_code in _RETRYABLE_STATUS
        code = FailureCode.UPSTREAM_TRANSIENT if retryable else FailureCode.REMOTE_REJECTED
        raise ContainerInvocationError(
            code,
            f"HTTP {status_code} {reason_phrase}",
            retryable=retryable,
            outcome="not_completed",
        )

    # Validate complete serialized result size to prevent SDK ABI_VIOLATION (PR #8 review fix)
    # Use actual serialization instead of estimation to handle all JSON types correctly
    # (int, bool, None, list, dict, str) and account for exact escaping overhead
    result_dict = {
        "status_code": status_code,
        "body": body,
        "headers": response_headers,
        "elapsed": elapsed,
    }
    actual_size = len(json.dumps(result_dict))

    if actual_size > 1_048_576:  # 1 MiB SDK limit
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            f"HTTP response too large: {actual_size} bytes (limit: 1 MiB)",
            retryable=False,
            outcome="not_completed",
        )

    return HttpOutput(
        status_code=status_code,
        body=body,
        headers=response_headers,
        elapsed=elapsed,
    )
