"""Fixtures that exercise SDK-built step images through their gRPC front doors."""

from __future__ import annotations

import re
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import grpc
import pytest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.service_types import ImageServices
from tests.support.step_type_artifacts import (
    ARTIFACTS,
    PLUGINS,
    load_inventory,
    load_workload_image,
)

_PODMAN = "podman"
_UPSTREAM_IP = "8.254.250.2"
_UPSTREAM_URL = "http://upstream.example:8000"
_UPSTREAM_SOURCE = Path(__file__).with_name("http_upstream.py").read_text(encoding="utf-8")


def _command(command: list[str], *, timeout: float = 45) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Could not run {' '.join(command)}: {error}") from error
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _container_logs(name: str) -> str:
    result = subprocess.run(
        [_PODMAN, "logs", name], capture_output=True, text=True, timeout=15, check=False
    )
    return (result.stdout + result.stderr).strip() or "<no container logs>"


def _container_state(name: str) -> str:
    result = subprocess.run(
        [_PODMAN, "inspect", "--format", "{{.State.Status}}", name],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.stdout.strip() or result.stderr.strip() or "unknown"


def _save_diagnostics(names: list[str], build_id: str, reason: str) -> Path:
    diagnostic_dir = ARTIFACTS / "diagnostics" / f"{build_id}-{uuid.uuid4().hex[:8]}"
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    (diagnostic_dir / "failure.txt").write_text(reason + "\n", encoding="utf-8")
    for name in names:
        state = _container_state(name)
        (diagnostic_dir / f"{name}.state.txt").write_text(state + "\n", encoding="utf-8")
        (diagnostic_dir / f"{name}.log").write_text(_container_logs(name), encoding="utf-8")
    return diagnostic_dir


def _published_port(name: str, container_port: int) -> int:
    result = _command([_PODMAN, "port", name, f"{container_port}/tcp"])
    for line in result.splitlines():
        match = re.search(r"(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)$", line.strip())
        if match:
            return int(match.group(1))
    raise RuntimeError(f"Podman returned an unexpected published port for {name}: {result!r}")


def _wait_upstream(name: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    last_error = "upstream has not started"
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                _PODMAN,
                "exec",
                name,
                "python3.12",
                "-c",
                "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=1).close()",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return
        last_error = (result.stderr or result.stdout).strip() or "connection refused"
        if _container_state(name) not in {"running", "unknown"}:
            break
        time.sleep(0.25)
    raise RuntimeError(
        f"Controlled upstream {name} did not start: {last_error}\n"
        f"container logs:\n{_container_logs(name)}"
    )


def _wait_ready(
    name: str, port: int, timeout: float = 40
) -> tuple[grpc.Channel, container_pb2_grpc.ContainerServiceStub]:
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = container_pb2_grpc.ContainerServiceStub(channel)
    deadline = time.monotonic() + timeout
    last_error = "gRPC Health has not responded"
    while time.monotonic() < deadline:
        try:
            response = stub.Health(container_pb2.HealthRequest(), timeout=2)
            if response.ready:
                return channel, stub
            last_error = "gRPC Health returned not-ready"
        except grpc.RpcError as error:
            last_error = f"{error.code().name}: {error.details()}"
        if _container_state(name) not in {"running", "unknown"}:
            break
        time.sleep(0.4)
    channel.close()
    raise RuntimeError(
        f"Container {name} did not become gRPC-ready on 127.0.0.1:{port}; "
        f"state={_container_state(name)}, last error={last_error}\n"
        f"container logs:\n{_container_logs(name)}"
    )


@pytest.fixture(scope="session")
def image_services(request: pytest.FixtureRequest) -> Iterator[ImageServices]:
    inventory = load_inventory()
    run_id = uuid.uuid4().hex[:10]
    network = f"syntara-step-test-{run_id}"
    upstream_network = f"{network}-upstream"
    names: list[str] = []
    channels: list[grpc.Channel] = []
    created_networks: list[str] = []

    with ExitStack() as loaded_images:
        images = {
            plugin: loaded_images.enter_context(load_workload_image(plugin, inventory))
            for plugin in PLUGINS
        }
        try:
            _command([_PODMAN, "network", "create", network])
            created_networks.append(network)
            _command(
                [
                    _PODMAN,
                    "network",
                    "create",
                    "--internal",
                    "--subnet",
                    "8.254.250.0/24",
                    upstream_network,
                ]
            )
            created_networks.append(upstream_network)
            upstream_name = f"syntara-test-upstream-{run_id}"
            names.append(upstream_name)
            _command(
                [
                    _PODMAN,
                    "run",
                    "--detach",
                    "--name",
                    upstream_name,
                    "--network",
                    upstream_network,
                    "--ip",
                    _UPSTREAM_IP,
                    "--entrypoint",
                    "python3.12",
                    images["script-python"],
                    "-u",
                    "-c",
                    _UPSTREAM_SOURCE,
                ]
            )
            _wait_upstream(upstream_name)

            stubs: dict[str, container_pb2_grpc.ContainerServiceStub] = {}
            for plugin in PLUGINS:
                name = f"syntara-test-{plugin}-{run_id}"
                names.append(name)
                command = [
                    _PODMAN,
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--publish",
                    "127.0.0.1::50051",
                ]
                if plugin == "http-request":
                    command.extend(["--add-host", f"upstream.example:{_UPSTREAM_IP}"])
                command.append(images[plugin])
                _command(command)
                if plugin == "http-request":
                    _command([_PODMAN, "network", "connect", upstream_network, name])
                port = _published_port(name, 50051)
                channel, stubs[plugin] = _wait_ready(name, port)
                channels.append(channel)

            yield ImageServices(
                http=stubs["http-request"],
                python=stubs["script-python"],
                bash=stubs["script-bash"],
                upstream_url=_UPSTREAM_URL,
            )
        except BaseException as error:
            diagnostic_dir = _save_diagnostics(
                names, str(inventory.get("buildId", "unknown")), str(error)
            )
            print(f"Integration diagnostics saved to {diagnostic_dir}")
            raise
        finally:
            for channel in channels:
                channel.close()

            if request.session.testsfailed:
                diagnostic_dir = _save_diagnostics(
                    names,
                    str(inventory.get("buildId", "unknown")),
                    f"{request.session.testsfailed} integration test(s) failed",
                )
                print(f"Integration diagnostics saved to {diagnostic_dir}")

            for name in reversed(names):
                subprocess.run(
                    [_PODMAN, "rm", "--force", name],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            for network_name in reversed(created_networks):
                subprocess.run(
                    [_PODMAN, "network", "rm", network_name],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )


@pytest.fixture(scope="session")
def http_stub(image_services: ImageServices) -> container_pb2_grpc.ContainerServiceStub:
    return image_services.http


@pytest.fixture(scope="session")
def python_script_stub(image_services: ImageServices) -> container_pb2_grpc.ContainerServiceStub:
    return image_services.python


@pytest.fixture(scope="session")
def bash_script_stub(image_services: ImageServices) -> container_pb2_grpc.ContainerServiceStub:
    return image_services.bash


@pytest.fixture(scope="session")
def test_upstream(image_services: ImageServices) -> str:
    return image_services.upstream_url
