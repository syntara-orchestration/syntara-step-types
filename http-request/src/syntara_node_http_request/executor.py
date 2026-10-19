"""HTTP execution logic for the HTTP request step."""

from __future__ import annotations

import ipaddress
import json
import time
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qsl, urlparse, urlsplit, urlunsplit

import httpx
from syntara_plugin.runtime import ContainerInvocationError, FailureCode

from ._models import HttpInput, HttpOutput

_MAX_RESPONSE_BYTES = 1_048_576
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


def _validate_url_no_ssrf(url: str) -> None:
    """Reject URLs targeting private, link-local, loopback, or non-HTTP schemes."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            "URL must use http:// or https:// scheme.",
            retryable=False,
            outcome="not_started",
        )
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ContainerInvocationError(
                FailureCode.POLICY_DENIED,
                "URL targets a private or reserved address.",
                retryable=False,
                outcome="not_started",
            )
    except ValueError:
        pass  # hostname rather than IP literal — allow


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


def _bound_response_body(response: httpx.Response) -> Any:
    content = response.content[:_MAX_RESPONSE_BYTES]
    try:
        return json.loads(content)
    except Exception:  # noqa: BLE001
        return content.decode("utf-8", errors="replace")


def execute_http_request(inputs: HttpInput) -> HttpOutput:
    """Make a bounded, SSRF-safe HTTP request and return structured output."""
    _validate_url_no_ssrf(inputs.url)
    url, params = _resolve_query_params(inputs.url, dict(inputs.query_params))

    json_body = inputs.body if isinstance(inputs.body, dict) else None
    str_body = inputs.body.encode() if isinstance(inputs.body, str) else None

    start = time.time()
    try:
        with httpx.Client(follow_redirects=inputs.follow_redirects, trust_env=False) as client:
            response = client.request(
                method=inputs.method.value,
                url=url,
                headers=dict(inputs.headers),
                params=params,
                json=json_body,
                content=str_body,
                timeout=float(inputs.timeout_seconds),
            )
    except httpx.TimeoutException as exc:
        raise ContainerInvocationError(
            FailureCode.TIMEOUT,
            "HTTP request timed out.",
            retryable=True,
            outcome="not_completed",
        ) from exc
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
