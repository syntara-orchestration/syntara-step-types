"""HTTP request step: makes an outbound HTTP call from a container."""

from __future__ import annotations

import threading

from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    ExecutionContext,
    FailureCode,
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


class HttpRequestStep(ActionStep[HttpInput, HttpOutput]):
    """Make an outbound HTTP request and return the response as typed output."""

    def run(self, inputs: HttpInput, context: ExecutionContext) -> HttpOutput:
        if not _EXECUTE_SEMAPHORE.acquire(blocking=False):
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                "Server busy: too many concurrent executions",
                retryable=True,
                outcome="not_started",
            )
        try:
            return execute_http_request(inputs)
        finally:
            _EXECUTE_SEMAPHORE.release()
