"""HTTP execution logic for the HTTP request step."""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from http import HTTPStatus
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


def _validate_url_no_ssrf(url: str) -> None:
    """Reject URLs targeting private, link-local, loopback, or non-HTTP schemes.

    Validates both IP literals and resolves hostnames to check all addresses.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            "URL must use http:// or https:// scheme.",
            retryable=False,
            outcome="not_started",
        )

    host = parsed.hostname
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
        return
    except ValueError:
        pass  # Not an IP literal, continue to DNS resolution

    # Resolve hostname and check all resolved addresses
    try:
        addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
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

    # Check all resolved addresses
    for _family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
            if _is_blocked_address(addr):
                raise ContainerInvocationError(
                    FailureCode.POLICY_DENIED,
                    f"Hostname {host} resolves to blocked address: {ip_str}",
                    retryable=False,
                    outcome="not_started",
                )
        except ValueError:
            # Should not happen with getaddrinfo results, but be safe
            continue


def _resolve_query_params(
    url: str, query_params: dict[str, str]
) -> tuple[str, dict[str, str] | None]:
    if not query_params:
        return url, None
    split = urlsplit(url)
    existing = dict(parse_qsl(split.query, keep_blank_values=True))
    merged = {**existing, **query_params}
    clean = urlunsplit((split.scheme, split.netloc, split.path, "", split.fragment))
    return clean, merged


def _stream_response_body_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Stream response body with size limit enforced during reading."""
    chunks: list[bytes] = []
    total_read = 0

    try:
        for chunk in response.iter_bytes(chunk_size=65536):
            chunk_size = len(chunk)
            if total_read + chunk_size > max_bytes:
                remaining = max_bytes - total_read
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                raise ContainerInvocationError(
                    FailureCode.POLICY_DENIED,
                    f"Response body exceeds {max_bytes} byte limit.",
                    retryable=False,
                    outcome="not_completed",
                )
            chunks.append(chunk)
            total_read += chunk_size
    finally:
        response.close()

    return b"".join(chunks)


def _bound_response_body(response: httpx.Response) -> Any:
    """Parse response body as JSON or text, enforcing size limit during streaming."""
    content = _stream_response_body_limited(response, _MAX_RESPONSE_BYTES)
    try:
        return json.loads(content)
    except Exception:  # noqa: BLE001
        return content.decode("utf-8", errors="replace")


def _make_request_with_manual_redirects(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str] | None,
    json_body: dict[str, Any] | None,
    str_body: bytes | None,
    timeout: float,
    follow_redirects: bool,
) -> httpx.Response:
    """Make HTTP request, manually following and validating redirects."""
    current_url = url
    redirect_count = 0

    while True:
        response = client.request(
            method=method,
            url=current_url,
            headers=headers,
            params=params if redirect_count == 0 else None,  # Only send params on initial request
            json=json_body if redirect_count == 0 else None,  # Only send body on initial request
            content=str_body if redirect_count == 0 else None,
            timeout=timeout,
            follow_redirects=False,  # Manually handle redirects
        )

        # If not a redirect or redirects not enabled, return the response
        if not follow_redirects or not response.is_redirect:
            return response

        # Check redirect limit
        redirect_count += 1
        if redirect_count > _MAX_REDIRECTS:
            response.close()
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                f"Too many redirects (>{_MAX_REDIRECTS})",
                retryable=False,
                outcome="not_completed",
            )

        # Get redirect location
        location = response.headers.get("location")
        if not location:
            response.close()
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                "Redirect response missing Location header",
                retryable=False,
                outcome="not_completed",
            )

        # Resolve relative redirects
        next_url = str(response.next_request.url) if response.next_request else location

        # Validate redirect destination against SSRF policy
        _validate_url_no_ssrf(next_url)

        response.close()
        current_url = next_url
        # Use GET for subsequent redirect requests (standard redirect behavior)
        method = "GET"


def execute_http_request(inputs: HttpInput) -> HttpOutput:
    """Make a bounded, SSRF-safe HTTP request and return structured output."""
    _validate_url_no_ssrf(inputs.url)
    url, params = _resolve_query_params(inputs.url, dict(inputs.query_params))

    json_body = inputs.body if isinstance(inputs.body, dict) else None
    str_body = inputs.body.encode() if isinstance(inputs.body, str) else None

    start = time.time()
    try:
        with httpx.Client(trust_env=False) as client:
            response = _make_request_with_manual_redirects(
                client=client,
                method=inputs.method.value,
                url=url,
                headers=dict(inputs.headers),
                params=params,
                json_body=json_body,
                str_body=str_body,
                timeout=float(inputs.timeout_seconds),
                follow_redirects=inputs.follow_redirects,
            )
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

    elapsed = time.time() - start
    body = _bound_response_body(response)

    if response.status_code >= HTTPStatus.BAD_REQUEST:
        retryable = response.status_code in _RETRYABLE_STATUS
        code = FailureCode.UPSTREAM_TRANSIENT if retryable else FailureCode.REMOTE_REJECTED
        raise ContainerInvocationError(
            code,
            f"HTTP {response.status_code} {response.reason_phrase}",
            retryable=retryable,
            outcome="not_completed",
        )

    return HttpOutput(
        status_code=response.status_code,
        body=body,
        headers=dict(response.headers),
        elapsed=elapsed,
    )
