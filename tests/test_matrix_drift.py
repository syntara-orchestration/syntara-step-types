"""Drift checks: docs/compatibility-matrix.md output freezes vs plugin manifests.

The compatibility matrix records the frozen business output fields for each
plugin. These tests parse the machine-readable ``output_freeze`` blocks in the
matrix and assert them against each plugin manifest's ``spec.output`` JSON
Schema, so a change to one without the other fails CI (``make sdk-plugin-drift``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MATRIX = _REPO_ROOT / "docs" / "compatibility-matrix.md"

# One entry per ```yaml output_freeze block in the compatibility matrix.
_FREEZE_RE = re.compile(r"```yaml\n(output_freeze:\n(?:  .*\n)+?)```", re.MULTILINE)


def _load_freezes() -> list[dict]:
    blocks = _FREEZE_RE.findall(_MATRIX.read_text())
    assert blocks, "no output_freeze blocks found in compatibility matrix"
    entries = [yaml.safe_load(block)["output_freeze"] for block in blocks]
    names = [entry["plugin"] for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, (
        f"duplicate output_freeze blocks, only the last would be checked: {duplicates}"
    )
    return entries


def _manifest_output(plugin: str) -> dict:
    manifest = yaml.safe_load((_REPO_ROOT / plugin / "manifest.yaml").read_text())
    output = manifest["spec"]["output"]
    assert output["kind"] == "inline", f"{plugin}: expected inline output schema"
    return output["value"]


def _freeze_view(schema: dict) -> tuple[set[str], dict[str, set[str]]]:
    """Return (required, properties) for a flat schema or a oneOf union.

    For a union, a field is "required" when any branch requires it, and its
    declared types are the union of the branch types (ignoring null, which
    only marks branch-optional nullability).
    """
    if "oneOf" not in schema:
        required = set(schema.get("required", []))
        properties = {
            name: {props.get("type")} - {None}
            for name, props in schema.get("properties", {}).items()
        }
        return required, properties

    branches = schema["oneOf"]
    required: set[str] = set()
    properties: dict[str, set[str]] = {}
    for branch in branches:
        required |= set(branch.get("required", []))
        for name, props in branch.get("properties", {}).items():
            declared = props.get("type")
            types = set(declared) if isinstance(declared, list) else {declared} - {None}
            properties.setdefault(name, set()).update(types - {"null"})
    return required, properties


@pytest.fixture(scope="module")
def freezes() -> dict[str, dict]:
    entries = _load_freezes()
    return {entry["plugin"]: entry for entry in entries}


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request", "aap-workflow"])
def test_every_plugin_has_a_freeze(freezes: dict[str, dict], plugin: str) -> None:
    assert plugin in freezes, f"{plugin}: no output_freeze block in the matrix"


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request", "aap-workflow"])
def test_required_fields_match_manifest(freezes: dict[str, dict], plugin: str) -> None:
    required, _properties = _freeze_view(_manifest_output(plugin))
    frozen = set(freezes[plugin]["required"])
    assert frozen == required, (
        f"{plugin}: matrix freeze {sorted(frozen)} != manifest required {sorted(required)}"
    )


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request", "aap-workflow"])
def test_optional_fields_exist_in_manifest(freezes: dict[str, dict], plugin: str) -> None:
    required, properties = _freeze_view(_manifest_output(plugin))
    optional = set(freezes[plugin].get("optional", []))
    assert optional <= set(properties) - required, (
        f"{plugin}: matrix optional fields {sorted(optional)} not optional "
        f"properties in manifest ({sorted(set(properties) - required)})"
    )


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request", "aap-workflow"])
def test_no_undeclared_frozen_fields(freezes: dict[str, dict], plugin: str) -> None:
    """Every manifest output property is accounted for in the freeze."""
    _required, properties = _freeze_view(_manifest_output(plugin))
    declared = set(freezes[plugin]["required"]) | set(freezes[plugin].get("optional", []))
    assert set(properties) == declared, (
        f"{plugin}: manifest properties {sorted(properties)} not fully "
        f"covered by matrix freeze {sorted(declared)}"
    )


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request", "aap-workflow"])
def test_frozen_field_types_match_manifest(freezes: dict[str, dict], plugin: str) -> None:
    """The matrix freezes a JSON Schema type per field; assert it against the manifest."""
    _required, properties = _freeze_view(_manifest_output(plugin))
    frozen = freezes[plugin]
    types = frozen.get("types")
    assert isinstance(types, dict), f"{plugin}: no types map in the output_freeze block"
    fields = set(frozen["required"]) | set(frozen.get("optional", []))
    assert set(types) == fields, (
        f"{plugin}: types map {sorted(types)} does not cover the frozen fields {sorted(fields)}"
    )
    for field, expected in types.items():
        declared = properties.get(field, set())
        assert expected in declared or expected is None, (
            f"{plugin}: matrix freezes {field} as {expected!r}, "
            f"manifest declares {sorted(declared)!r}"
        )
