"""Shared helpers for container gRPC integration tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc


def execute_events(
    stub: container_pb2_grpc.ContainerServiceStub,
    request: container_pb2.ExecuteRequest,
    *,
    timeout: float,
):
    """Yield ExecutionEvents from the bidi Execute stream.

    Bidi protocol (contracts 0.1.0a4): the client opens the stream with the
    ExecuteRequest. A credential request fails the test: these steps declare
    no credentials, so the server is never expected to ask for one.
    """
    messages = iter([container_pb2.ExecuteClientMessage(start=request)])
    for message in stub.Execute(messages, timeout=timeout):
        if message.HasField("credential_request"):
            pytest.fail(f"unexpected credential request — {message.credential_request.name}")
        yield message.event


def execute_output(
    stub: container_pb2_grpc.ContainerServiceStub,
    request: container_pb2.ExecuteRequest,
    *,
    timeout: float,
) -> dict[str, Any]:
    for event in execute_events(stub, request, timeout=timeout):
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
