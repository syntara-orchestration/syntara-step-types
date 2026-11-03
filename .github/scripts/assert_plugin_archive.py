"""Assert that an SDK-built archive matches its source plugin and image build."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REFERENCE = re.compile(r"^(.+)@(sha256:[0-9a-f]{64})$")
_WORKLOAD_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.syntara.plugin.manifest.v1+yaml"
_PLUGIN_DIRECTORIES = {
    name: Path(name) for name in ("http-request", "script-python", "script-bash")
}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _within(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved_path = (resolved_root / relative_path).resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"path must stay within {resolved_root}: {relative_path}")
    return resolved_path


def _read_yaml(plugin_directory: Path, relative_path: str) -> dict[str, Any]:
    path = _within(plugin_directory, relative_path)
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), relative_path)


def _read_json_blob(archive: tarfile.TarFile, descriptor: dict[str, Any]) -> dict[str, Any]:
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("archive contains an invalid SHA-256 descriptor")
    member = archive.extractfile(f"blobs/sha256/{digest.removeprefix('sha256:')}")
    if member is None:
        raise ValueError(f"archive is missing descriptor blob {digest}")
    return _mapping(json.loads(member.read()), f"archive blob {digest}")


def _expected_target_identities(plugin_directory: Path, plugin: dict[str, Any]) -> set[str]:
    metadata = _mapping(plugin.get("metadata"), "plugin metadata")
    namespace = metadata.get("namespace")
    plugin_name = metadata.get("name")
    spec = _mapping(plugin.get("spec"), "plugin spec")
    targets = spec.get("targets")
    if not isinstance(namespace, str) or not isinstance(plugin_name, str):
        raise ValueError("source plugin needs a namespace and name")
    if not isinstance(targets, list) or not targets:
        raise ValueError("source plugin needs at least one target")

    return {
        _target_identity(plugin_directory, target_path, namespace, plugin_name)
        for target_path in targets
    }


def _target_identity(
    plugin_directory: Path,
    target_path: Any,
    namespace: str,
    plugin_name: str,
) -> str:
    if not isinstance(target_path, str):
        raise ValueError("source plugin target paths must be strings")
    action = _read_yaml(plugin_directory, target_path)
    action_metadata = _mapping(action.get("metadata"), f"{target_path} metadata")
    action_name = action_metadata.get("name")
    if not isinstance(action_name, str):
        raise ValueError(f"{target_path} must have a target name")
    return f"{namespace}.{plugin_name}.{action_name}"


def _source_configuration(
    plugin_directory: Path,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    source_plugin = _read_yaml(plugin_directory, "plugin.yaml")
    settings = _read_yaml(plugin_directory, "syntara-plugin.yaml")
    metadata = _mapping(source_plugin.get("metadata"), "source plugin metadata")
    spec = _mapping(source_plugin.get("spec"), "source plugin spec")
    workload = _mapping(spec.get("workload"), "source plugin workload")
    workload_image_id = workload.get("image")
    if not isinstance(workload_image_id, str):
        raise ValueError("source plugin must declare a workload image")

    registry = _mapping(settings.get("registry"), "plugin registry settings")
    origin = registry.get("origin")
    repository = registry.get("workloadRepository")
    if not isinstance(origin, str) or not isinstance(repository, str):
        raise ValueError("plugin settings must declare registry origin and workload repository")
    expected_repository = f"{urlparse(origin).netloc}/{repository}"
    return source_plugin, metadata, workload_image_id, expected_repository


def _build_results(
    plugin_directory: Path,
    plugin_name: str,
    workload_tag: str,
    expected_repository: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    artifact_prefix = f"{plugin_name}-{workload_tag}"
    build_path = _within(plugin_directory, f"dist/{artifact_prefix}-build.json")
    inspect_path = _within(plugin_directory, f"dist/{artifact_prefix}-inspect.json")
    archive_path = _within(plugin_directory, f"dist/{artifact_prefix}.oci.tar")
    build_result = _mapping(json.loads(build_path.read_text(encoding="utf-8")), "build result")
    inspect_result = _mapping(
        json.loads(inspect_path.read_text(encoding="utf-8")), "inspect result"
    )

    artifact_digest = build_result.get("artifactDigest")
    if not isinstance(artifact_digest, str) or not _SHA256.fullmatch(artifact_digest):
        raise ValueError("SDK build result has an invalid artifact digest")
    image_reference = build_result.get("workloadImage")
    match = (
        _IMAGE_REFERENCE.fullmatch(image_reference) if isinstance(image_reference, str) else None
    )
    if match is None or match.group(1) != expected_repository:
        raise ValueError(f"build result has an unexpected workload image: {image_reference!r}")
    if inspect_result.get("artifactDigest") != artifact_digest:
        raise ValueError("inspect result does not match the artifact produced by build")
    return build_result, inspect_result, image_reference, str(archive_path)


def _archived_plugin_descriptor(archive_path: str) -> tuple[dict[str, Any], str]:
    with tarfile.open(archive_path, mode="r:*") as archive:
        index_file = archive.extractfile("index.json")
        if index_file is None:
            raise ValueError("archive is missing its OCI index")
        index = _mapping(json.loads(index_file.read()), "OCI index")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("archive must contain exactly one plugin artifact")
        manifest = _read_json_blob(archive, _mapping(manifests[0], "OCI manifest descriptor"))
        layers = manifest.get("layers")
        if not isinstance(layers, list):
            raise ValueError("archive OCI manifest has no layers")
        plugin_descriptor_ref = next(
            (
                layer
                for layer in layers
                if isinstance(layer, dict) and layer.get("mediaType") == _PLUGIN_MANIFEST_MEDIA_TYPE
            ),
            None,
        )
        if plugin_descriptor_ref is None:
            raise ValueError("archive is missing its plugin descriptor")
        plugin_descriptor = _read_json_blob(archive, plugin_descriptor_ref)
        descriptor_digest = plugin_descriptor_ref.get("digest")
    if not isinstance(descriptor_digest, str) or not _SHA256.fullmatch(descriptor_digest):
        raise ValueError("archive plugin descriptor has an invalid digest")
    return plugin_descriptor, descriptor_digest


def _assert_plugin_identity(
    plugin_directory: Path,
    source_plugin: dict[str, Any],
    plugin_descriptor: dict[str, Any],
) -> None:
    source_metadata = _mapping(source_plugin.get("metadata"), "source plugin metadata")
    archived_metadata = _mapping(plugin_descriptor.get("metadata"), "archived plugin metadata")
    for key in ("namespace", "name", "version"):
        if archived_metadata.get(key) != source_metadata.get(key):
            raise ValueError(f"archive plugin metadata {key!r} differs from plugin.yaml")

    archived_spec = _mapping(plugin_descriptor.get("spec"), "archived plugin spec")
    archived_targets = archived_spec.get("targets")
    if not isinstance(archived_targets, list):
        raise ValueError("archive plugin descriptor has no targets")
    actual_identities = {
        _mapping(target, "archived target").get("identity") for target in archived_targets
    }
    if actual_identities != _expected_target_identities(plugin_directory, source_plugin):
        raise ValueError("archive target identities differ from plugin.yaml")


def _assert_workload_image(
    source_plugin: dict[str, Any], plugin_descriptor: dict[str, Any], image_reference: str
) -> None:
    source_spec = _mapping(source_plugin.get("spec"), "source plugin spec")
    source_workload = _mapping(source_spec.get("workload"), "source plugin workload")
    workload_image_id = source_workload.get("image")
    archived_spec = _mapping(plugin_descriptor.get("spec"), "archived plugin spec")
    archived_workloads = _mapping(archived_spec.get("workloads"), "archived workloads")
    archived_workload = _mapping(archived_workloads.get(workload_image_id), "archived workload")
    if archived_workload.get("image") != image_reference:
        raise ValueError("archive workload digest differs from the SDK build result")


def _workload_tag(value: str) -> str:
    if not _WORKLOAD_TAG.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "workload tag contains invalid path or image-tag characters"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin", choices=tuple(_PLUGIN_DIRECTORIES))
    parser.add_argument("workload_tag", type=_workload_tag, help="Tag used for this workload build")
    args = parser.parse_args()

    plugin_directory = _within(Path.cwd(), str(_PLUGIN_DIRECTORIES[args.plugin]))
    source_plugin, _, _, expected_repository = _source_configuration(plugin_directory)
    _, inspect_result, image_reference, archive_path = _build_results(
        plugin_directory, args.plugin, args.workload_tag, expected_repository
    )
    plugin_descriptor, descriptor_digest = _archived_plugin_descriptor(archive_path)
    if inspect_result.get("compiledDigest") != descriptor_digest:
        raise ValueError("inspect result does not match the archived plugin descriptor")
    _assert_plugin_identity(plugin_directory, source_plugin, plugin_descriptor)
    _assert_workload_image(source_plugin, plugin_descriptor, image_reference)
    print(f"Verified {args.plugin}: {image_reference}")


if __name__ == "__main__":
    main()
