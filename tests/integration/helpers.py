"""Shared helpers for container gRPC integration tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc


def execute_output(
    stub: container_pb2_grpc.ContainerServiceStub,
    request: container_pb2.ExecuteRequest,
    *,
    timeout: float,
) -> dict[str, Any]:
    for event in stub.Execute(request, timeout=timeout):
        if event.HasField("result"):
            if event.result.status_code:
                pytest.fail(
                    f"Step returned error result — {event.result.error_result_json.decode()}"
                )
            return json.loads(event.result.output_json)
        if event.HasField("failure"):
            pytest.fail(
                f"Step returned failure — code={event.failure.code} message={event.failure.message}"
            )
    pytest.fail("Execute stream closed without a result or failure event")
