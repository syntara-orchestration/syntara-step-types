"""Fixtures that exercise SDK-built step images through their gRPC front doors."""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import grpc
import pytest
from syntara_plugin.contracts import registration_manifest
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.service_types import TEST_BEARER_TOKEN, ImageServices
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
                "import socket; "
                "[socket.create_connection(('127.0.0.1', port), timeout=1).close() "
                "for port in (8000, 8443)]",
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


def _create_test_tls_certificate(directory: Path) -> tuple[Path, Path]:
    certificate = directory / "upstream.crt"
    private_key = directory / "upstream.key"
    result = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
            "-days",
            "2",
            "-subj",
            "/CN=upstream.example",
            "-addext",
            "subjectAltName=DNS:upstream.example",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Could not create the integration TLS certificate: {result.stderr}")
    # The test upstream runs as UID 65532 from the script-python image. OpenSSL
    # creates its private key as 0600, which that container user cannot read
    # through the bind mount. This key is generated for this test run only.
    private_key.chmod(0o644)
    return certificate, private_key


def _create_runtime_tls_material(directory: Path) -> tuple[Path, Path, Path, Path]:
    """Create short-lived mTLS material for the SDK runtime gRPC listener."""
    directory.mkdir(parents=True)
    ca_key = directory / "ca.key"
    ca_certificate = directory / "client-ca.crt"
    extensions = directory / "runtime.ext"
    extensions.write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
        "keyUsage=digitalSignature,keyEncipherment\n",
        encoding="utf-8",
    )
    _command(["openssl", "genrsa", "-out", str(ca_key), "2048"])
    _command(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-out",
            str(ca_certificate),
            "-subj",
            "/CN=syntara-runtime-test-ca",
            "-days",
            "2",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ]
    )

    credentials: dict[str, Path] = {}
    for name in ("server", "client"):
        key = directory / f"{name}.key"
        request = directory / f"{name}.csr"
        certificate = directory / f"{name}.crt"
        _command(["openssl", "genrsa", "-out", str(key), "2048"])
        _command(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(key),
                "-out",
                str(request),
                "-subj",
                f"/CN={name}",
            ]
        )
        _command(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(request),
                "-CA",
                str(ca_certificate),
                "-CAkey",
                str(ca_key),
                "-CAcreateserial",
                "-out",
                str(certificate),
                "-days",
                "2",
                "-extfile",
                str(extensions),
            ]
        )
        # The workload runs as UID 65532 and the files are only mounted into
        # throwaway test containers for the duration of this test session.
        key.chmod(0o644)
        credentials[f"{name}_certificate"] = certificate
        credentials[f"{name}_key"] = key

    for path in (ca_key, extensions, directory / "client-ca.srl"):
        path.unlink(missing_ok=True)
    directory.chmod(0o755)
    return (
        ca_certificate,
        credentials["server_certificate"],
        credentials["client_certificate"],
        credentials["client_key"],
    )


def _write_runtime_documents(plugin: str, inventory: dict, directory: Path) -> Path:
    """Derive the SDK server's resolved and registration files from its artifact."""
    entry = inventory["plugins"][plugin]
    archive_path = ARTIFACTS.parent / entry["pluginArchive"]
    with tarfile.open(archive_path, mode="r:*") as archive:
        index = json.load(archive.extractfile("index.json"))
        manifest_digest = index["manifests"][0]["digest"].split(":", 1)[1]
        manifest_path = f"blobs/sha256/{manifest_digest}"
        artifact_manifest = json.load(archive.extractfile(manifest_path))
        descriptor_layer = next(
            layer
            for layer in artifact_manifest["layers"]
            if layer["annotations"].get("org.opencontainers.image.title") == "plugin.manifest.yaml"
        )
        descriptor_digest = descriptor_layer["digest"].split(":", 1)[1]
        descriptor = json.load(archive.extractfile(f"blobs/sha256/{descriptor_digest}"))

    workload_ids = {
        target["runtime"]["workload"]
        for target in descriptor["spec"]["targets"]
        if target.get("runtime", {}).get("kind") == "custom-runner"
    }
    if len(workload_ids) != 1:
        raise RuntimeError(f"Expected one custom-runner workload in {plugin}'s plugin artifact")
    registration = registration_manifest(descriptor, next(iter(workload_ids)))
    resolved = {
        "bindings": [
            {"stepRevisionId": revision, "runtimeKind": "custom-runner", **binding}
            for revision, binding in registration["steps"].items()
        ]
    }
    directory.mkdir(parents=True)
    (directory / "registration.json").write_text(
        json.dumps(registration, sort_keys=True), encoding="utf-8"
    )
    (directory / "resolved.json").write_text(json.dumps(resolved, sort_keys=True), encoding="utf-8")
    directory.chmod(0o755)
    return directory


def _wait_ready(
    name: str,
    port: int,
    *,
    root_certificate: Path,
    client_certificate: Path,
    client_key: Path,
    timeout: float = 40,
) -> tuple[grpc.Channel, container_pb2_grpc.ContainerServiceStub]:
    credentials = grpc.ssl_channel_credentials(
        root_certificates=root_certificate.read_bytes(),
        private_key=client_key.read_bytes(),
        certificate_chain=client_certificate.read_bytes(),
    )
    channel = grpc.secure_channel(f"127.0.0.1:{port}", credentials)
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

    with (
        ExitStack() as loaded_images,
        tempfile.TemporaryDirectory(prefix="syntara-http-tls-") as tls_directory,
    ):
        images = {
            plugin: loaded_images.enter_context(load_workload_image(plugin, inventory))
            for plugin in PLUGINS
        }
        try:
            certificate, private_key = _create_test_tls_certificate(Path(tls_directory))
            runtime_cert_dir = Path(tls_directory) / "runtime-certs"
            (
                runtime_ca,
                runtime_server_certificate,
                runtime_client_certificate,
                runtime_client_key,
            ) = _create_runtime_tls_material(runtime_cert_dir)
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
                    "--volume",
                    f"{certificate}:/tmp/upstream.crt:ro",
                    "--volume",
                    f"{private_key}:/tmp/upstream.key:ro",
                    "--env",
                    "TLS_CERT_FILE=/tmp/upstream.crt",
                    "--env",
                    "TLS_KEY_FILE=/tmp/upstream.key",
                    "--env",
                    f"EXPECTED_BEARER_TOKEN={TEST_BEARER_TOKEN}",
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
                runtime_directory = _write_runtime_documents(
                    plugin, inventory, Path(tls_directory) / f"runtime-{plugin}"
                )
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
                    "--volume",
                    f"{runtime_directory}:/tmp/syntara-runtime:ro",
                    "--volume",
                    f"{runtime_cert_dir}:/tmp/syntara-certs:ro",
                    "--env",
                    "SYNTARA_RESOLVED_EXECUTION=/tmp/syntara-runtime/resolved.json",
                    "--env",
                    "SYNTARA_REGISTRATION_MANIFEST=/tmp/syntara-runtime/registration.json",
                    "--env",
                    "SYNTARA_RUNTIME_GRPC_TLS_CERT=/tmp/syntara-certs/server.crt",
                    "--env",
                    "SYNTARA_RUNTIME_GRPC_TLS_KEY=/tmp/syntara-certs/server.key",
                    "--env",
                    "SYNTARA_RUNTIME_GRPC_TLS_CLIENT_CA=/tmp/syntara-certs/client-ca.crt",
                ]
                if plugin == "http-request":
                    command.extend(["--add-host", f"upstream.example:{_UPSTREAM_IP}"])
                command.append(images[plugin])
                _command(command)
                if plugin == "http-request":
                    user_id = _command([_PODMAN, "exec", name, "id", "-u"]).strip()
                    if user_id != "65532":
                        raise RuntimeError(
                            f"HTTP workload must run as UID 65532, got UID {user_id!r}"
                        )
                    _command([_PODMAN, "network", "connect", upstream_network, name])
                    _command([_PODMAN, "cp", str(certificate), f"{name}:/tmp/upstream-ca.crt"])
                    # The workload itself runs as UID 65532. Use root only for
                    # this test setup step because certifi's bundled CA file is
                    # root-owned in the image; the HTTP server stays non-root.
                    _command(
                        [
                            _PODMAN,
                            "exec",
                            "--user",
                            "0",
                            name,
                            "python3.12",
                            "-c",
                            "import certifi; from pathlib import Path; "
                            "bundle=Path(certifi.where()); "
                            "bundle.write_bytes(bundle.read_bytes()+b'\\n'+"
                            "Path('/tmp/upstream-ca.crt').read_bytes())",
                        ]
                    )
                port = _published_port(name, 50051)
                channel, stubs[plugin] = _wait_ready(
                    name,
                    port,
                    root_certificate=runtime_ca,
                    client_certificate=runtime_client_certificate,
                    client_key=runtime_client_key,
                )
                channels.append(channel)

            yield ImageServices(
                http=stubs["http-request"],
                python=stubs["script-python"],
                bash=stubs["script-bash"],
                upstream_url=_UPSTREAM_URL,
                https_upstream_url="https://upstream.example:8443",
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
