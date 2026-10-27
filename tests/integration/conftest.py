"""Fixtures that start and stop real step containers for integration testing."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid

import grpc
import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

CONTAINER_ENGINE = os.environ.get("CONTAINER_ENGINE", "podman")
TEST_TAG = os.environ.get("TEST_TAG", "dev")
REGISTRY = os.environ.get("REGISTRY", "localhost")


def _image(step: str) -> str:
    return f"{REGISTRY}/syntara-node-{step}:{TEST_TAG}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_container(step: str, host_port: int) -> str:
    name = f"syntara-{step}-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            CONTAINER_ENGINE,
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{host_port}:50051",
            _image(step),
        ],
        check=True,
        capture_output=True,
    )
    return name


def _stop_container(name: str) -> None:
    subprocess.run(
        [CONTAINER_ENGINE, "rm", "-f", name],
        check=False,
        capture_output=True,
    )


def _wait_ready(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            channel = grpc.insecure_channel(f"127.0.0.1:{port}")
            stub = container_pb2_grpc.ContainerServiceStub(channel)
            stub.Health(container_pb2.HealthRequest(), timeout=2)
            channel.close()
            return
        except grpc.RpcError as exc:
            last_exc = exc
            time.sleep(0.5)
        finally:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass
    raise TimeoutError(
        f"Container on port {port} did not become ready within {timeout}s: {last_exc}"
    )


def _container_fixture(step: str):
    """Build a session-scoped pytest fixture that manages a step container."""

    @pytest.fixture(scope="session")
    def _fixture():
        port = _free_port()
        name = _start_container(step, port)
        channel: grpc.Channel | None = None
        try:
            _wait_ready(port)
            channel = grpc.insecure_channel(f"127.0.0.1:{port}")
            stub = container_pb2_grpc.ContainerServiceStub(channel)
            yield stub
        finally:
            if channel is not None:
                channel.close()
            _stop_container(name)

    return _fixture


script_stub = _container_fixture("script")
http_stub = _container_fixture("http-request")
