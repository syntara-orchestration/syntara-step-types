"""Check inactive Konflux templates against the available workspace components."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Validate template syntax, component coverage and build paths before onboarding."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    nodes = [name for name in project["tool"]["uv"]["workspace"]["members"] if not name.startswith("_")]
    directory = ROOT / "konflux/templates"
    expected = {f"{node}-{event}.yaml.in" for node in nodes for event in ("pull-request", "push")}
    actual = {path.name for path in directory.glob("*.yaml.in")}
    if actual != expected:
        message = f"Template matrix mismatch: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        raise SystemExit(message)
    for node in nodes:
        for event in ("pull-request", "push"):
            path = directory / f"{node}-{event}.yaml.in"
            config = yaml.safe_load(path.read_text())
            if config["apiVersion"] != "tekton.dev/v1" or config["kind"] != "PipelineRun":
                raise SystemExit(f"{path.name}: expected a Tekton v1 PipelineRun")
            component = config["metadata"]["labels"]["appstudio.openshift.io/component"]
            if component != f"syntara-node-{node}":
                raise SystemExit(f"{path.name}: incorrect component label")
            params = {param["name"]: param["value"] for param in config["spec"]["params"]}
            required = {"dockerfile": f"{node}/Containerfile", "path-context": ".", "hermetic": "true"}
            for name, value in required.items():
                if params.get(name) != value:
                    raise SystemExit(f"{path.name}: {name} must be {value!r}")
            if not (ROOT / params["dockerfile"]).is_file():
                raise SystemExit(f"{path.name}: Containerfile does not exist")
            prefetch = json.loads(params["prefetch-input"])
            if not any(item.get("type") == "pip" and item.get("path") == "." for item in prefetch):
                raise SystemExit(f"{path.name}: missing workspace pip prefetch input")
    print(f"Validated {len(expected)} inactive templates; tenant and hermetic build validation remain required.")


if __name__ == "__main__":
    main()
