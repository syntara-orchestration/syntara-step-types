"""Build and verify step workload archives through the installed SDK CLI."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "support"))

from step_type_artifacts import (  # noqa: E402
    ARTIFACTS,
    INVENTORY,
    PLUGINS,
    build_platform,
    source_fingerprint,
)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _under(path_value: str, root: Path, label: str) -> Path:
    path = Path(path_value).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError(f"SDK {label} escaped the isolated build directory: {path}")
    if not path.is_file():
        raise RuntimeError(f"SDK {label} is missing: {path}")
    return path


def _run_cli(command: list[str], *, cwd: Path, output_path: Path, expect_ok: bool = True) -> dict:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1200,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        output_path.write_text(stdout, encoding="utf-8")
        output_path.with_suffix(".stderr.log").write_text(stderr, encoding="utf-8")
        raise RuntimeError(
            f"SDK command timed out after 1200 seconds: {' '.join(command)}\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        ) from error
    output_path.write_text(result.stdout, encoding="utf-8")
    output_path.with_suffix(".stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            f"SDK command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"SDK command returned invalid JSON.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"SDK command returned a non-object JSON result: {payload!r}")
    if expect_ok and payload.get("ok") is not True:
        raise RuntimeError(f"SDK command did not report success: {payload}")
    return payload


def _build_plugin(plugin: str, cli: str, build_root: Path, platform: str) -> dict:
    print(f"Building {plugin} workload through syntara-plugin build for {platform}.", flush=True)
    plugin_root = ROOT / plugin
    output_root = build_root / plugin
    output_root.mkdir()
    source_root = output_root / "source"
    shutil.copytree(
        plugin_root,
        source_root,
        ignore=shutil.ignore_patterns("dist", "__pycache__", ".venv", ".pytest_cache"),
    )
    sdk_dist = source_root / "dist"

    settings = yaml.safe_load((source_root / "syntara-plugin.yaml").read_text(encoding="utf-8"))
    binding_path = source_root / "runtime-bindings.yaml"

    settings["manifest"] = str((source_root / "plugin.yaml").resolve())
    settings["build"]["runtimeBindings"] = str(binding_path.resolve())
    settings["build"]["artifactOutput"] = str((sdk_dist / f"{plugin}.oci.tar").resolve())
    workload = settings["build"]["workloads"][plugin]
    workload["context"] = str(ROOT.resolve())
    workload["containerfile"] = str((plugin_root / "Containerfile").resolve())
    workload["platform"] = platform
    settings_path = output_root / "syntara-plugin.yaml"
    settings_path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")

    plugin_archive = sdk_dist / f"{plugin}.oci.tar"
    build_result_path = output_root / "build.json"
    build_result = _run_cli(
        [
            cli,
            "build",
            str(source_root / "plugin.yaml"),
            "--settings",
            str(settings_path),
            "--runtime-bindings",
            str(binding_path),
            "--output",
            str(plugin_archive),
            "--json",
        ],
        cwd=source_root,
        output_path=build_result_path,
    )

    inspect_result_path = output_root / "inspect.json"
    inspect_result = _run_cli(
        [cli, "inspect", str(plugin_archive), "--settings", str(settings_path), "--json"],
        cwd=source_root,
        output_path=inspect_result_path,
        expect_ok=False,
    )

    source_plugin = yaml.safe_load((source_root / "plugin.yaml").read_text(encoding="utf-8"))
    workload_id = next(iter(source_plugin["spec"]["workloads"]))
    images = build_result.get("images")
    if not isinstance(images, list):
        raise RuntimeError(f"SDK build for {plugin} returned no workload images.")
    image = next(
        (
            entry
            for entry in images
            if isinstance(entry, dict) and entry.get("workload") == workload_id
        ),
        None,
    )
    if not isinstance(image, dict):
        raise RuntimeError(f"SDK build did not build workload {workload_id!r}.")
    expected_repository = (
        f"{urlparse(settings['registry']['origin']).netloc}/"
        f"{settings['registry']['workloadRepository']}"
    )
    reference = image.get("reference")
    digest = image.get("digest")
    if (
        not isinstance(reference, str)
        or not reference.startswith(f"{expected_repository}@sha256:")
        or not isinstance(digest, str)
        or reference.rsplit("@", 1)[-1] != digest
    ):
        raise RuntimeError(
            f"SDK build returned an invalid workload reference for {plugin}: {image}"
        )
    if image.get("platform") != platform:
        raise RuntimeError(
            f"SDK build produced {image.get('platform')!r} for {plugin}; expected {platform}."
        )

    workload_archive = _under(image.get("archive", ""), output_root, f"{plugin} workload archive")
    runtime_binding_archive = _under(
        build_result.get("bindingArchive", ""), output_root, f"{plugin} runtime-binding archive"
    )
    plugin_archive = _under(str(plugin_archive), output_root, f"{plugin} plugin archive")
    if build_result.get("pluginArtifactDigest") != inspect_result.get("artifactDigest"):
        raise RuntimeError(f"SDK inspect digest differs from the build for {plugin}.")

    # Reuse the repository's archive verifier against the isolated build directory.
    helper = ROOT / ".github" / "scripts" / "assert_plugin_archive.py"
    verification = subprocess.run(
        [
            sys.executable,
            str(helper),
            plugin,
            "--build-result",
            str(build_result_path),
            "--inspect-result",
            str(inspect_result_path),
            "--plugin-archive",
            str(plugin_archive),
            "--artifact-root",
            str(output_root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if verification.returncode:
        raise RuntimeError(
            f"SDK artifact verification failed for {plugin}.\n"
            f"stdout:\n{verification.stdout}\nstderr:\n{verification.stderr}"
        )
    print(verification.stdout.strip(), flush=True)

    registry = settings["registry"]
    expected_repository = f"{urlparse(registry['origin']).netloc}/{registry['workloadRepository']}"
    return {
        "imageReference": image["reference"],
        "platform": image["platform"],
        "pluginArchive": plugin_archive.relative_to(ROOT).as_posix(),
        "pluginArtifactDigest": build_result["pluginArtifactDigest"],
        "pluginArchiveSha256": _file_digest(plugin_archive),
        "runtimeBindingArchive": runtime_binding_archive.relative_to(ROOT).as_posix(),
        "runtimeBindingArchiveSha256": _file_digest(runtime_binding_archive),
        "workloadArchive": workload_archive.relative_to(ROOT).as_posix(),
        "workloadDigest": digest,
        "workloadArchiveSha256": _file_digest(workload_archive),
        "workloadRepository": expected_repository,
        "workloadId": workload_id,
    }


def main() -> int:
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        with (ARTIFACTS / ".build.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            INVENTORY.unlink(missing_ok=True)
            cli = shutil.which("syntara-plugin")
            if cli is None:
                raise RuntimeError("syntara-plugin is missing; run make install first.")
            platform = build_platform()
            fingerprint = source_fingerprint()
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            build_root = ARTIFACTS / "runs" / run_id
            build_root.mkdir(parents=True)

            plugins = {
                plugin: _build_plugin(plugin, cli, build_root, platform) for plugin in PLUGINS
            }
            if source_fingerprint() != fingerprint:
                raise RuntimeError(
                    "Source files changed during the image build; rerun make test-build."
                )

            inventory = {
                "schemaVersion": 1,
                "buildId": run_id,
                "createdAt": datetime.now(UTC).isoformat(),
                "sdkVersion": importlib.metadata.version("syntara-plugin-sdk"),
                "platform": platform,
                "sourceFingerprint": fingerprint,
                "buildDirectory": build_root.relative_to(ROOT).as_posix(),
                "plugins": plugins,
            }
            temporary_inventory = ARTIFACTS / f".build-inventory-{run_id}.tmp"
            temporary_inventory.write_text(
                json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary_inventory.replace(INVENTORY)
            print(f"SDK workload archives verified for {platform}; inventory: {INVENTORY}")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"test-build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
