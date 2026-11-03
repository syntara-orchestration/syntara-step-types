"""HTTP request step: makes an outbound HTTP call from a container."""

from __future__ import annotations

import threading

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
                return execute_http_request(inputs, context.cancellation)
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
