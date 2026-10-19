"""HTTP request step: makes an outbound HTTP call from a container."""

from __future__ import annotations

from syntara_plugin.runtime import ExecutionContext, TaskStep

from ._models import HttpInput, HttpMethod, HttpOutput
from .executor import execute_http_request

__all__ = [
    "HttpInput",
    "HttpMethod",
    "HttpOutput",
    "HttpRequestStep",
]


class HttpRequestStep(TaskStep[HttpInput, HttpOutput]):
    """Make an outbound HTTP request and return the response as typed output."""

    def run(self, inputs: HttpInput, context: ExecutionContext) -> HttpOutput:
        return execute_http_request(inputs)
