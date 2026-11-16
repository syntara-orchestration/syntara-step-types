"""HTTP request step: makes an outbound HTTP call from a container."""

from __future__ import annotations

import threading
from urllib.parse import urlsplit

from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    ErrorCategory,
    ExecutionContext,
    FailureCode,
    StepFailure,
)

from ._models import HttpInput, HttpMethod, HttpOutput
from .executor import execute_http_request

__all__ = [
    "HttpInput",
    "HttpMethod",
    "HttpOutput",
    "HttpRequestStep",
]

# Hard admission limit: reserve 2 out of 10 gRPC threads for Cancel and Health RPCs.
# Without this, 10 simultaneous Execute calls starve all control-plane operations.
_EXECUTE_SEMAPHORE = threading.Semaphore(8)

_ERROR_CATEGORIES = {
    FailureCode.REMOTE_REJECTED: ErrorCategory.BUSINESS,
    FailureCode.UPSTREAM_TRANSIENT: ErrorCategory.DEPENDENCY,
    FailureCode.TIMEOUT: ErrorCategory.TIMEOUT,
    FailureCode.POLICY_DENIED: ErrorCategory.POLICY,
    FailureCode.CANCELLED: ErrorCategory.CANCELLED,
}
_HTTP_ENDPOINT_CONTEXT_KEY = "httpEndpoint"


def _credential_destination_matches(url: str, endpoint: object) -> bool:
    """Require credentialed requests to target the selected HTTPS integration origin."""
    if not isinstance(endpoint, str):
        return False
    try:
        target = urlsplit(url)
        configured = urlsplit(endpoint)
        target_host = target.hostname
        configured_host = configured.hostname
        if (
            target.scheme.lower() != "https"
            or configured.scheme.lower() != "https"
            or target_host is None
            or configured_host is None
            or target.username is not None
            or target.password is not None
            or configured.username is not None
            or configured.password is not None
            or configured.path not in {"", "/"}
            or configured.query
            or configured.fragment
        ):
            return False
        return (target_host.casefold(), target.port or 443) == (
            configured_host.casefold(),
            configured.port or 443,
        )
    except ValueError:
        return False


class HttpRequestStep(ActionStep[HttpInput, HttpOutput]):
    """Make an outbound HTTP request and return the response as typed output."""

    def run(self, inputs: HttpInput, context: ExecutionContext) -> HttpOutput:
        if not _EXECUTE_SEMAPHORE.acquire(blocking=False):
            raise StepFailure(
                1,
                "Server busy: too many concurrent executions",
                "Server busy: too many concurrent executions",
                code=FailureCode.UPSTREAM_TRANSIENT.value,
                category=ErrorCategory.DEPENDENCY,
                retryable=True,
            )
        try:
            try:
                request_inputs = inputs
                if inputs.credential_name is not None:
                    if not _credential_destination_matches(
                        inputs.url, context.runtime_context.get(_HTTP_ENDPOINT_CONTEXT_KEY)
                    ):
                        raise ContainerInvocationError(
                            FailureCode.POLICY_DENIED,
                            "Credentialed HTTP requests must use the configured HTTPS endpoint.",
                            retryable=False,
                            outcome="not_started",
                        )
                    credential = context.credential(inputs.credential_name).read()
                    token = credential.get("token")
                    if not isinstance(token, str) or not token:
                        raise ContainerInvocationError(
                            FailureCode.POLICY_DENIED,
                            "The configured HTTP credential is unavailable.",
                            retryable=False,
                            outcome="not_started",
                        )
                    headers = dict(inputs.headers)
                    for name in tuple(headers):
                        if name.casefold() == "authorization":
                            del headers[name]
                    headers["Authorization"] = f"Bearer {token}"
                    request_inputs = inputs.model_copy(update={"headers": headers})
                return execute_http_request(request_inputs, context.cancellation)
            except ContainerInvocationError as exc:
                code = exc.code.value if isinstance(exc.code, FailureCode) else str(exc.code)
                category = _ERROR_CATEGORIES.get(exc.code, ErrorCategory.INTERNAL)
                raise StepFailure(
                    1,
                    exc.safe_message,
                    exc.safe_message,
                    code=code,
                    category=category,
                    retryable=exc.retryable,
                ) from exc
        finally:
            _EXECUTE_SEMAPHORE.release()
