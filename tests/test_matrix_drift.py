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


@pytest.fixture(scope="module")
def freezes() -> dict[str, dict]:
    entries = _load_freezes()
    return {entry["plugin"]: entry for entry in entries}


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request"])
def test_every_plugin_has_a_freeze(freezes: dict[str, dict], plugin: str) -> None:
    assert plugin in freezes, f"{plugin}: no output_freeze block in the matrix"


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request"])
def test_required_fields_match_manifest(freezes: dict[str, dict], plugin: str) -> None:
    schema = _manifest_output(plugin)
    required = set(schema.get("required", []))
    frozen = set(freezes[plugin]["required"])
    assert frozen == required, (
        f"{plugin}: matrix freeze {sorted(frozen)} != manifest required {sorted(required)}"
    )


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request"])
def test_optional_fields_exist_in_manifest(freezes: dict[str, dict], plugin: str) -> None:
    schema = _manifest_output(plugin)
    properties = set(schema.get("properties", []))
    required = set(schema.get("required", []))
    optional = set(freezes[plugin].get("optional", []))
    assert optional <= properties - required, (
        f"{plugin}: matrix optional fields {sorted(optional)} not optional "
        f"properties in manifest ({sorted(properties - required)})"
    )


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request"])
def test_no_undeclared_frozen_fields(freezes: dict[str, dict], plugin: str) -> None:
    """Every manifest output property is accounted for in the freeze."""
    schema = _manifest_output(plugin)
    properties = set(schema.get("properties", []))
    declared = set(freezes[plugin]["required"]) | set(freezes[plugin].get("optional", []))
    assert properties == declared, (
        f"{plugin}: manifest properties {sorted(properties)} not fully "
        f"covered by matrix freeze {sorted(declared)}"
    )


@pytest.mark.parametrize("plugin", ["script-python", "script-bash", "http-request"])
def test_frozen_field_types_match_manifest(freezes: dict[str, dict], plugin: str) -> None:
    """The matrix freezes a JSON Schema type per field; assert it against the manifest."""
    schema = _manifest_output(plugin)
    properties = schema.get("properties", {})
    frozen = freezes[plugin]
    types = frozen.get("types")
    assert isinstance(types, dict), f"{plugin}: no types map in the output_freeze block"
    fields = set(frozen["required"]) | set(frozen.get("optional", []))
    assert set(types) == fields, (
        f"{plugin}: types map {sorted(types)} does not cover the frozen fields {sorted(fields)}"
    )
    for field, expected in types.items():
        actual = properties.get(field, {}).get("type")
        assert actual == expected, (
            f"{plugin}: matrix freezes {field} as {expected!r}, manifest declares {actual!r}"
        )
