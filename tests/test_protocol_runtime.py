"""Exercise the runtime contract without any concrete workflow step package."""

import asyncio
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import grpc
import pytest
from pydantic import Field, ValidationError
from sqlmodel import SQLModel
from syntara_node_protocol import node_pb2 as pb
from syntara_node_protocol import node_pb2_grpc as rpc
from syntara_node_protocol.client import NodeRpcError, invoke
from syntara_node_protocol.codec import CHANNEL_OPTIONS, MAX_MESSAGE_BYTES, decode_event, encode_request
from syntara_node_runtime.runtime import MAX_INVOCATION_TIMEOUT_SECONDS, Invocation, NodeFailure
from syntara_node_runtime.server import create_server


class EchoInput(SQLModel):
    """Test-only SDK input with controllable lifecycle behavior."""

    value: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False
    fail: bool = False
    progress: str | None = None
    log_secret: str | None = None


class EchoOutput(SQLModel):
    """Retain arbitrary JSON values across the protocol."""

    value: dict[str, Any]


class EchoNode:
    """Minimal node interface used to test the shared server independently."""

    input_model = EchoInput
    output_model = EchoOutput

    def run(self, inputs, context) -> dict:
        if inputs.log_secret:

            def raise_logged_value() -> None:
                raise ValueError(inputs.log_secret)

            try:
                raise_logged_value()
            except ValueError:
                context.logger.exception("SDK logger saw %s", inputs.log_secret)
        if inputs.progress:
            context.emit("heartbeat", {"message": inputs.progress})
        if inputs.wait and context.cancelled.wait(5):
            raise asyncio.CancelledError
        output = {"value": inputs.value}
        if inputs.fail:
            message = "fixture failed"
            raise NodeFailure(message, {"output": output}, type="FixtureError", non_retryable=True)
        return output


@pytest.fixture
def endpoint():
    server, service, port = create_server(EchoNode(), "127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}", options=CHANNEL_OPTIONS) as channel:
        yield channel, service
    service.cancelled.set()
    server.stop(2).wait(3)


def request(**inputs):
    return {"inputs": inputs, "timeout_seconds": 10}


def execute(channel, data, cancelled=None, progress=None):
    return invoke(
        channel,
        data,
        identity="test-id",
        progress=progress or (lambda _frame: None),
        cancelled=cancelled or threading.Event(),
        startup=2,
        grace=3,
    )


def test_readiness_and_json_roundtrip(endpoint):
    channel, _service = endpoint
    stub = rpc.NodeServiceStub(channel)
    health = stub.Health(pb.HealthRequest(), timeout=2)
    assert health.ready
    assert health.protocol_version == 1
    value = {"id": 1152921504606846977, "empty": None, "items": [True, "text"]}
    result = execute(channel, request(value=value))
    assert result["result"]["StatusCode"] == 0
    assert result["result"]["Result"] == {"value": value}
    assert not stub.Health(pb.HealthRequest(), timeout=2).ready


def test_partial_failure_output(endpoint):
    channel, _service = endpoint
    result = execute(channel, request(value={"partial": 42}, fail=True))
    assert result["result"]["Result"] == {"value": {"partial": 42}}
    assert result["error"] == {"type": "FixtureError", "retryable": False}


def test_second_execution_rejected(endpoint):
    channel, _service = endpoint
    execute(channel, request())
    with pytest.raises(grpc.RpcError) as error:
        list(rpc.NodeServiceStub(channel).Execute(encode_request(request(), "second"), timeout=2))
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS


def test_cancel_returns_terminal_result(endpoint):
    channel, service = endpoint
    cancelled = threading.Event()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(execute, channel, request(wait=True), cancelled)
        for _ in range(100):
            if service.invocation_id:
                break
            time.sleep(0.01)
        assert service.invocation_id == "test-id"
        cancelled.set()
        assert future.result(timeout=5)["error"]["type"] == "CancelledError"


@pytest.mark.parametrize("disconnect", [True, False])
def test_disconnect_or_deadline_cancels_work(endpoint, disconnect):
    channel, service = endpoint
    call = rpc.NodeServiceStub(channel).Execute(encode_request(request(wait=True), "test"), timeout=0.5)
    for _ in range(100):
        if service.invocation_id:
            break
        time.sleep(0.01)
    if disconnect:
        call.cancel()
    with pytest.raises(grpc.RpcError):
        list(call)
    assert service.finished.wait(3)
    assert service.cancelled.is_set()


def test_invalid_request_does_not_leak_credentials(endpoint):
    channel, service = endpoint
    message = encode_request(request(), "bad")
    message.credentials_json = b'"private-token"'
    with pytest.raises(grpc.RpcError) as error:
        list(rpc.NodeServiceStub(channel).Execute(message, timeout=2))
    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "private-token" not in error.value.details()
    assert service.invocation_id is None


def test_request_size_limit_before_submission(endpoint):
    channel, service = endpoint
    with pytest.raises(NodeRpcError, match="transport limit"):
        execute(channel, request(value={"data": "x" * MAX_MESSAGE_BYTES}))
    assert service.invocation_id is None


def test_progress_and_results_are_redacted(endpoint):
    channel, _service = endpoint
    data = request(value={"token": "private-token"}, progress="contains private-token")
    data["credentials"] = {"resolved": {"extra_vars": {"bearer_token": "private-token"}}}
    frames = []
    result = execute(channel, data, progress=frames.append)
    assert frames[0]["data"]["message"] == "contains [REDACTED]"
    assert result["result"]["Result"]["value"] == {"token": "[REDACTED]"}


def test_only_secret_settings_are_redacted_from_progress_and_results(endpoint):
    channel, _service = endpoint
    setting_a = "settings-value-a-123"
    setting_b = "settings-value-b-456"
    username = "automation-user"
    allowed_host = "controller.example.test"
    data = request(
        value={
            "message": f"setting is {setting_b}",
            "aap_username": username,
            "host": allowed_host,
        },
        progress=f"setting is {setting_a}",
    )
    data["settings"] = {
        "aap_token": setting_a,
        "aap_password": setting_b,
        "aap_username": username,
        "workflow_http_request_allowed_hosts": [allowed_host],
    }
    frames = []

    result = execute(channel, data, progress=frames.append)

    assert frames[0]["data"]["message"] == "setting is [REDACTED]"
    assert result["result"]["Result"]["value"] == {
        "message": "setting is [REDACTED]",
        "aap_username": username,
        "host": allowed_host,
    }


def test_sdk_logger_redacts_messages_and_exception_tracebacks(endpoint, caplog):
    channel, _service = endpoint
    logged_value = "settings-value-in-log"
    data = request(log_secret=logged_value)
    data["settings"] = {"aap_token": logged_value}

    result = execute(channel, data)
    logged = caplog.text

    assert result["result"]["StatusCode"] == 0
    assert logged_value not in logged
    assert "[REDACTED]" in logged
    assert "Traceback" not in logged


def test_invocation_timeout_has_a_24_hour_maximum():
    assert Invocation(timeout_seconds=MAX_INVOCATION_TIMEOUT_SECONDS).timeout_seconds == MAX_INVOCATION_TIMEOUT_SECONDS
    with pytest.raises(ValidationError):
        Invocation(timeout_seconds=MAX_INVOCATION_TIMEOUT_SECONDS + 1)


def test_input_validation_returns_safe_failure(endpoint):
    channel, _service = endpoint
    result = execute(channel, request(value="private-token"))
    assert result["error"]["type"] == "ValidationError"
    assert result["result"]["ErrorMessage"] == "Node execution failed"


def test_unencrypted_remote_listener_rejected():
    with pytest.raises(ValueError, match="mutual TLS"):
        create_server(EchoNode(), "0.0.0.0:50051")


def test_event_correlation_required():
    with pytest.raises(ValueError, match="invocation ID"):
        decode_event(pb.ExecutionEvent(invocation_id="wrong"), "expected")


def test_direct_connection_requires_client_certificate(tmp_path):
    cert, key = tmp_path / "tls.crt", tmp_path / "tls.key"
    openssl = shutil.which("openssl")
    assert openssl is not None
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-days",
            "1",
        ],
        check=True,
        capture_output=True,
    )
    credentials = grpc.ssl_server_credentials(
        [(key.read_bytes(), cert.read_bytes())], root_certificates=cert.read_bytes(), require_client_auth=True
    )
    server, _service, port = create_server(EchoNode(), "0.0.0.0:0", credentials=credentials)
    server.start()
    try:
        unauthenticated = grpc.ssl_channel_credentials(root_certificates=cert.read_bytes())
        with grpc.secure_channel(f"localhost:{port}", unauthenticated) as channel:
            with pytest.raises(grpc.RpcError):
                rpc.NodeServiceStub(channel).Health(pb.HealthRequest(), timeout=0.5)
        authenticated = grpc.ssl_channel_credentials(
            root_certificates=cert.read_bytes(), private_key=key.read_bytes(), certificate_chain=cert.read_bytes()
        )
        with grpc.secure_channel(f"localhost:{port}", authenticated) as channel:
            assert execute(channel, request())["result"]["StatusCode"] == 0
    finally:
        server.stop(2).wait(3)
