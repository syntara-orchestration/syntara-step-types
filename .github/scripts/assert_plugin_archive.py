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
_PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.syntara.plugin.manifest.v1+yaml"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


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

    identities = set()
    for target_path in targets:
        if not isinstance(target_path, str):
            raise ValueError("source plugin target paths must be strings")
        action = _read_yaml(plugin_directory / target_path)
        action_metadata = _mapping(action.get("metadata"), f"{target_path} metadata")
        action_name = action_metadata.get("name")
        if not isinstance(action_name, str):
            raise ValueError(f"{target_path} must have a target name")
        identities.add(f"{namespace}.{plugin_name}.{action_name}")
    return identities


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin", help="Plugin directory name")
    parser.add_argument("workload_tag", help="Tag used for this workload build")
    args = parser.parse_args()

    plugin_directory = Path(args.plugin)
    source_plugin = _read_yaml(plugin_directory / "plugin.yaml")
    settings = _read_yaml(plugin_directory / "syntara-plugin.yaml")
    metadata = _mapping(source_plugin.get("metadata"), "source plugin metadata")
    source_spec = _mapping(source_plugin.get("spec"), "source plugin spec")
    workload = _mapping(source_spec.get("workload"), "source plugin workload")
    workload_image_id = workload.get("image")
    if not isinstance(workload_image_id, str):
        raise ValueError("source plugin must declare a workload image")

    registry = _mapping(settings.get("registry"), "plugin registry settings")
    origin = registry.get("origin")
    repository = registry.get("workloadRepository")
    if not isinstance(origin, str) or not isinstance(repository, str):
        raise ValueError("plugin settings must declare registry origin and workload repository")
    parsed_origin = urlparse(origin)
    expected_repository = f"{parsed_origin.netloc}/{repository}"

    artifact_prefix = f"{args.plugin}-{args.workload_tag}"
    archive_path = plugin_directory / "dist" / f"{artifact_prefix}.oci.tar"
    build_result_path = plugin_directory / "dist" / f"{artifact_prefix}-build.json"
    inspect_result_path = plugin_directory / "dist" / f"{artifact_prefix}-inspect.json"
    build_result = _mapping(
        json.loads(build_result_path.read_text(encoding="utf-8")), "build result"
    )
    inspect_result = _mapping(
        json.loads(inspect_result_path.read_text(encoding="utf-8")), "inspect result"
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

    with tarfile.open(archive_path, mode="r:*") as archive:
        index = _mapping(json.loads(archive.extractfile("index.json").read()), "OCI index")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("archive must contain exactly one plugin artifact")
        image_manifest = _read_json_blob(archive, _mapping(manifests[0], "OCI manifest descriptor"))
        layers = image_manifest.get("layers")
        if not isinstance(layers, list):
            raise ValueError("archive OCI manifest has no layers")
        plugin_descriptor_ref = next(
            (
                _mapping(layer, "OCI layer descriptor")
                for layer in layers
                if isinstance(layer, dict) and layer.get("mediaType") == _PLUGIN_MANIFEST_MEDIA_TYPE
            ),
            None,
        )
        if plugin_descriptor_ref is None:
            raise ValueError("archive is missing its plugin descriptor")
        descriptor_digest = plugin_descriptor_ref.get("digest")
        plugin_descriptor = _read_json_blob(archive, plugin_descriptor_ref)

    if inspect_result.get("artifactDigest") != artifact_digest:
        raise ValueError("inspect result does not match the artifact produced by build")
    if inspect_result.get("compiledDigest") != descriptor_digest:
        raise ValueError("inspect result does not match the archived plugin descriptor")

    archived_metadata = _mapping(plugin_descriptor.get("metadata"), "archived plugin metadata")
    for key in ("namespace", "name", "version"):
        if archived_metadata.get(key) != metadata.get(key):
            raise ValueError(f"archive plugin metadata {key!r} differs from plugin.yaml")

    archived_spec = _mapping(plugin_descriptor.get("spec"), "archived plugin spec")
    archived_targets = archived_spec.get("targets")
    if not isinstance(archived_targets, list):
        raise ValueError("archive plugin descriptor has no targets")
    actual_identities = {
        _mapping(target, "archived target").get("identity") for target in archived_targets
    }
    expected_identities = _expected_target_identities(plugin_directory, source_plugin)
    if actual_identities != expected_identities:
        raise ValueError("archive target identities differ from plugin.yaml")

    archived_workloads = _mapping(archived_spec.get("workloads"), "archived workloads")
    archived_workload = _mapping(archived_workloads.get(workload_image_id), "archived workload")
    if archived_workload.get("image") != image_reference:
        raise ValueError("archive workload digest differs from the SDK build result")

    print(f"Verified {args.plugin}: {image_reference}")


if __name__ == "__main__":
    main()
