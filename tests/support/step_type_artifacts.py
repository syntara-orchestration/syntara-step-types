"""Build inventory validation and SDK image loading for integration tests."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".test-artifacts"
INVENTORY = ARTIFACTS / "build-inventory.json"
PLUGINS = ("http-request", "script-python", "script-bash")
_IMAGE_REFERENCE = re.compile(r"^(.+)@(sha256:[0-9a-f]{64})$")


def platform_for_podman() -> str:
    """Return the native Linux platform Podman reports for this connection."""
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{.Host.Arch}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f"\nPodman reported: {detail}" if detail else ""
        raise RuntimeError(
            "Podman is unavailable or its configured connection is not running. "
            "Start the intended Podman machine, then retry." + suffix
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "Podman is unavailable or its configured connection is not running. "
            "Start the intended Podman machine, then retry."
        ) from error

    architecture = result.stdout.strip().lower()
    aliases = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    if architecture not in aliases:
        raise RuntimeError(f"Podman reported unsupported host architecture: {architecture!r}")
    return f"linux/{aliases[architecture]}"


def build_platform() -> str:
    """Choose a native image platform or honor an explicit build override."""
    native_platform = platform_for_podman()
    override = os.environ.get("TEST_PLATFORM")
    if override is None:
        return native_platform
    if override not in {"linux/amd64", "linux/arm64"}:
        raise RuntimeError("TEST_PLATFORM must be linux/amd64 or linux/arm64.")
    return override


def source_fingerprint() -> str:
    """Hash source and build inputs, excluding generated state and VCS metadata."""
    excluded = {
        ".git",
        ".pytest_cache",
        ".test-artifacts",
        ".venv",
        "__pycache__",
        "dist",
        "test-results",
    }
    digest = hashlib.sha256()
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink():
            digest.update(relative.as_posix().encode())
            digest.update(b"\0")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _inventory_path(value: Any, label: str, build_root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Build inventory has no {label}; run make test-build.")
    candidate = (ROOT / value).resolve()
    if not candidate.is_relative_to(build_root):
        raise RuntimeError(f"Build inventory {label} escapes its build directory.")
    if not candidate.is_file():
        raise RuntimeError(f"Build inventory {label} is missing; run make test-build.")
    return candidate


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def load_inventory(*, check_runtime: bool = True) -> dict[str, Any]:
    """Read and verify the current SDK build inventory before using its images."""
    try:
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("No valid SDK image build inventory; run make test-build.") from error

    if not isinstance(inventory, dict):
        raise RuntimeError("Build inventory must be a JSON object; run make test-build.")
    if inventory.get("schemaVersion") != 1:
        raise RuntimeError("Unsupported test build inventory; run make test-build.")
    if inventory.get("sourceFingerprint") != source_fingerprint():
        raise RuntimeError("SDK build artifacts are stale; run make test-build again.")
    if inventory.get("sdkVersion") != importlib.metadata.version("syntara-plugin-sdk"):
        raise RuntimeError("The SDK changed since the image build; run make test-build again.")
    if check_runtime and inventory.get("platform") != platform_for_podman():
        raise RuntimeError(
            "The Podman platform changed since the image build; run make test-build."
        )

    plugins = inventory.get("plugins")
    if not isinstance(plugins, dict) or set(plugins) != set(PLUGINS):
        raise RuntimeError("The build inventory is incomplete; run make test-build.")
    build_root = (ROOT / str(inventory.get("buildDirectory", ""))).resolve()
    if not build_root.is_relative_to(ARTIFACTS.resolve()) or not build_root.is_dir():
        raise RuntimeError("The test build directory is missing; run make test-build.")

    from syntara_plugin.sdk.workload import WorkloadBuildError, workload_archive_digest

    for plugin in PLUGINS:
        entry = plugins[plugin]
        if not isinstance(entry, dict):
            raise RuntimeError(f"Build inventory entry for {plugin} is invalid.")
        archive = _inventory_path(
            entry.get("workloadArchive"), f"{plugin} workload archive", build_root
        )
        plugin_archive = _inventory_path(
            entry.get("pluginArchive"), f"{plugin} plugin archive", build_root
        )
        binding_archive = _inventory_path(
            entry.get("runtimeBindingArchive"), f"{plugin} binding archive", build_root
        )
        match = _IMAGE_REFERENCE.fullmatch(str(entry.get("imageReference", "")))
        if match is None:
            raise RuntimeError(f"{plugin} image reference is invalid; run make test-build.")
        if entry.get("workloadDigest") != match.group(2) or entry.get(
            "workloadRepository"
        ) != match.group(1):
            raise RuntimeError(f"{plugin} workload archive digest is invalid; run make test-build.")
        try:
            actual_workload_digest = workload_archive_digest(archive)
        except WorkloadBuildError as error:
            raise RuntimeError(
                f"{plugin} workload archive cannot be verified; run make test-build."
            ) from error
        if actual_workload_digest != match.group(2):
            raise RuntimeError(f"{plugin} workload archive digest is invalid; run make test-build.")
        for artifact, key, label in (
            (archive, "workloadArchiveSha256", "workload"),
            (plugin_archive, "pluginArchiveSha256", "plugin"),
            (binding_archive, "runtimeBindingArchiveSha256", "runtime-binding"),
        ):
            if entry.get(key) != _file_digest(artifact):
                raise RuntimeError(
                    f"{plugin} {label} archive integrity check failed; run make test-build."
                )
        if entry.get("platform") != inventory.get("platform"):
            raise RuntimeError(f"{plugin} workload platform does not match the inventory.")
    return inventory


@contextmanager
def load_workload_image(plugin: str, inventory: dict[str, Any]) -> Iterator[str]:
    """Load one verified SDK archive into Podman for the gRPC integration tests."""
    from syntara_plugin.sdk.workload import workload_archive_digest

    entry = inventory["plugins"][plugin]
    archive = ROOT / entry["workloadArchive"]
    reference_match = _IMAGE_REFERENCE.fullmatch(entry["imageReference"])
    if reference_match is None:
        raise RuntimeError(f"{plugin} image reference is invalid; run make test-build.")
    if workload_archive_digest(archive) != reference_match.group(2):
        raise RuntimeError(f"{plugin} workload archive digest changed; run make test-build.")

    image_reference, config_digest = _workload_archive_load_metadata(archive)
    loaded = False
    try:
        try:
            result = subprocess.run(
                ["podman", "load", "--input", str(archive)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"Podman could not load the {plugin} workload archive.") from error
        if result.returncode:
            raise RuntimeError(
                f"Podman could not load the {plugin} workload archive: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        loaded = True

        try:
            inspected = subprocess.run(
                ["podman", "image", "inspect", "--format", "{{.Id}}", image_reference],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"Could not inspect the loaded {plugin} workload image.") from error
        actual_digest = inspected.stdout.strip().removeprefix("sha256:")
        expected_digest = config_digest.removeprefix("sha256:")
        if inspected.returncode or actual_digest != expected_digest:
            detail = (inspected.stderr or inspected.stdout).strip()
            raise RuntimeError(
                f"Loaded {plugin} image does not match its SDK archive: {detail or actual_digest}"
            )

        yield image_reference
    finally:
        if loaded:
            subprocess.run(
                ["podman", "image", "rm", "--ignore", image_reference],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )


def _workload_archive_load_metadata(archive_path: Path) -> tuple[str, str]:
    """Read the SDK-generated image reference and config digest from an OCI archive."""
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = {member.name: member for member in archive.getmembers()}
            index_member = members.get("index.json")
            if index_member is None or not index_member.isfile() or index_member.size > 1024 * 1024:
                raise RuntimeError("workload OCI archive has no bounded index.json")
            index_file = archive.extractfile(index_member)
            if index_file is None:
                raise RuntimeError("workload OCI archive index.json cannot be read")
            index = json.loads(index_file.read())
            manifests = index.get("manifests")
            if not isinstance(manifests, list) or len(manifests) != 1:
                raise RuntimeError("workload OCI archive must contain one image manifest")
            descriptor = manifests[0]
            annotations = descriptor.get("annotations")
            reference = annotations.get("org.opencontainers.image.ref.name")
            manifest_digest = descriptor.get("digest")
            if (
                not isinstance(reference, str)
                or not reference
                or any(character.isspace() for character in reference)
                or not isinstance(manifest_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_digest) is None
            ):
                raise RuntimeError("workload OCI archive image descriptor is invalid")

            manifest_name = f"blobs/sha256/{manifest_digest.removeprefix('sha256:')}"
            manifest_member = members.get(manifest_name)
            if (
                manifest_member is None
                or not manifest_member.isfile()
                or manifest_member.size > 16 * 1024 * 1024
            ):
                raise RuntimeError("workload OCI archive manifest is missing or oversized")
            manifest_file = archive.extractfile(manifest_member)
            if manifest_file is None:
                raise RuntimeError("workload OCI archive manifest cannot be read")
            manifest = json.loads(manifest_file.read())
            config = manifest.get("config")
            config_digest = config.get("digest") if isinstance(config, dict) else None
            if (
                not isinstance(config_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", config_digest) is None
            ):
                raise RuntimeError("workload OCI archive config digest is invalid")
            return reference, config_digest
    except (OSError, tarfile.TarError, json.JSONDecodeError, AttributeError, KeyError) as error:
        raise RuntimeError("Could not inspect workload OCI archive metadata.") from error
