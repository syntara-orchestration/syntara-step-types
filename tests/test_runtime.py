"""Tests of the public node process contract and failure boundaries."""

import json
import tomllib
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator
from syntara_node_runtime.runtime import Invocation, execute
from syntara_node_script import Node

ROOT = Path(__file__).resolve().parents[1]
NODES = [
    name
    for name in tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]["members"]
    if not name.startswith("_")
]


@pytest.mark.parametrize("name", NODES)
def test_example_inputs_match_manifest(name):
    manifest = yaml.safe_load((ROOT / name / "manifest.yaml").read_text())
    example = json.loads((ROOT / name / "example.json").read_text())
    Draft7Validator(manifest["spec"]["inputs"]).validate(example["inputs"])


def test_available_manifests_validate():
    schema = json.loads((ROOT / "schemas/common-definitions.json").read_text())
    validator = Draft7Validator({"$ref": "#/definitions/NodeTypeManifest", **schema})
    for name in NODES:
        validator.validate(yaml.safe_load((ROOT / name / "manifest.yaml").read_text()))
        assert (ROOT / name / "Containerfile").is_file()


def test_script_failure_preserves_output():
    frames = []
    frame = execute(
        Node(),
        Invocation(inputs={"language": "bash", "code": "printf partial; printf failure >&2; exit 7"}),
        frames.append,
    )
    assert frame["result"]["StatusCode"] == 1
    assert frame["result"]["Result"]["return_code"] == 7
    assert frame["result"]["Result"]["stdout"] == "partial"
    assert frame["error"]["retryable"] is False
