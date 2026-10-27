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

    # Use httpx.URL.host for the normalized hostname — httpx converts IDN/unicode to
    # punycode ACE form, matching what request.url.host returns in _pin_request_dns.
    # This prevents a DNS-rebinding bypass where urlparse and httpx return different
    # forms of the same IDNA hostname, causing a pin-map miss.
    host = httpx.URL(url).host
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

    # Resolve hostname and check all resolved addresses
    try:
        addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        # Distinguish temporary DNS failures from permanent ones
        if exc.errno in (socket.EAI_AGAIN, socket.EAI_NONAME, socket.EAI_FAIL):
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
    clean = urlunsplit((split.scheme, split.netloc, split.path, "", split.fragment))
    return clean, merged


def _stream_response_body_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Stream response body with size limit enforced during reading."""
    chunks: list[bytes] = []
    total_read = 0

    for chunk in response.iter_bytes(chunk_size=65536):
        chunk_size = len(chunk)
        if total_read + chunk_size > max_bytes:
            remaining = max_bytes - total_read
            if remaining > 0:
                chunks.append(chunk[:remaining])
            response.close()
            raise ContainerInvocationError(
                FailureCode.POLICY_DENIED,
                f"Response body exceeds {max_bytes} byte limit.",
                retryable=False,
                outcome="not_completed",
            )
        chunks.append(chunk)
        total_read += chunk_size

    return b"".join(chunks)


def _bound_response_body(response: httpx.Response) -> Any:
    """Parse response body as JSON or text, enforcing size limit during streaming."""
    content = _stream_response_body_limited(response, _MAX_RESPONSE_BYTES)
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

    # Read request body before creating the new request.  Accessing request.content
    # on an HTTPX Request that holds an unread stream (e.g. a redirect next_request)
    # raises RequestNotRead; calling read() first materialises the bytes.
    try:
        body = request.content
    except httpx.RequestNotRead:
        request.read()
        body = request.content

    # Create new request with pinned URL
    new_request = httpx.Request(
        method=request.method,
        url=new_url_str,
        headers=dict(request.headers),
        content=body,
        extensions=request.extensions,
    )

    # Set Host header to original hostname, preserving non-default ports.
    # HTTP/1.1 requires host:port when the port is non-standard.
    default_port = {"http": 80, "https": 443}.get(parsed.scheme, 0)
    if parsed.port and parsed.port != default_port:
        new_request.headers["host"] = f"{hostname}:{parsed.port}"
    else:
        new_request.headers["host"] = hostname

    # For HTTPS, set SNI server_hostname extension
    if parsed.scheme == "https":
        new_request.extensions["sni_hostname"] = hostname

    return new_request


def _make_request_with_validated_redirects(
    client: httpx.Client,
    request: httpx.Request,
    follow_redirects: bool,
    pinned_hosts: dict[str, str],
) -> httpx.Response:
    """Make HTTP request with SSRF-validated redirects.

    Uses HTTPX's next_request for proper credential stripping and 307/308 handling.

    Args:
        client: HTTPX client to use for requests
        request: Initial request to send
        follow_redirects: Whether to follow redirect responses
        pinned_hosts: Map of hostname -> IP to pin DNS and prevent rebinding
    """
    redirect_count = 0

    while True:
        # Pin DNS for this request if needed
        pinned_request = _pin_request_dns(request, pinned_hosts)

        # Send the request with streaming to avoid buffering
        response = client.send(pinned_request, stream=True)

        # Force header read before accessing next_request (PR #8 review fix)
        # This ensures the response is in a valid state for redirect processing
        _ = response.status_code
        _ = response.headers

        # If not following redirects or not a redirect, return
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

        # Get HTTPX's properly constructed next request
        # This handles credential stripping, 307/308 method preservation, etc.
        next_request = response.next_request
        if next_request is None:
            response.close()
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                "Redirect response missing or invalid Location header",
                retryable=False,
                outcome="not_completed",
            )

        # Validate redirect destination against SSRF policy and get pinned IP.
        # Key by httpx-normalized hostname to match _pin_request_dns lookup.
        next_url = str(next_request.url)
        _, pinned_ip = _validate_url_no_ssrf(next_url)
        if pinned_ip:
            host_key = httpx.URL(next_url).host
            if host_key:
                pinned_hosts[host_key] = pinned_ip

        # Restore credentials stripped by HTTPX due to IP/hostname origin mismatch.
        # HTTPX computed next_request against the pinned IP URL, so it treated a
        # same-hostname absolute redirect as cross-origin and stripped Authorization/Cookie.
        # Compare the real (unpinned) hostnames to decide whether to re-add them.
        current_host = httpx.URL(str(request.url)).host
        next_host = httpx.URL(next_url).host
        if current_host and next_host == current_host:
            for cred_header in ("authorization", "cookie"):
                if cred_header in request.headers and cred_header not in next_request.headers:
                    next_request.headers[cred_header] = request.headers[cred_header]

        response.close()
        request = next_request


def execute_http_request(inputs: HttpInput) -> HttpOutput:
    """Make a bounded, SSRF-safe HTTP request and return structured output."""
    # Validate initial URL and get pinned IP to prevent DNS rebinding attacks
    _, pinned_ip = _validate_url_no_ssrf(inputs.url)
    url, params = _resolve_query_params(inputs.url, dict(inputs.query_params))
    headers = dict(inputs.headers)

    # Build pinned hosts map keyed by httpx-normalized hostname to match _pin_request_dns.
    pinned_hosts: dict[str, str] = {}
    if pinned_ip:
        host_key = httpx.URL(inputs.url).host
        if host_key:
            pinned_hosts[host_key] = pinned_ip

    # Prepare request body
    json_body = inputs.body if isinstance(inputs.body, dict) else None
    str_body = inputs.body.encode() if isinstance(inputs.body, str) else None

    start = time.time()
    try:
        with httpx.Client(trust_env=False) as client:
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
            response = _make_request_with_validated_redirects(
                client=client,
                request=request,
                follow_redirects=inputs.follow_redirects,
                pinned_hosts=pinned_hosts,
            )

            # Read response body INSIDE the client context to avoid "Bad file descriptor"
            elapsed = time.time() - start
            body = _bound_response_body(response)
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
