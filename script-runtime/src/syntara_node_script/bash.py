"""Container entrypoint for the Bash script plugin."""

from __future__ import annotations

from pathlib import Path

from syntara_plugin.runtime import StepBindings
from syntara_plugin.runtime.server import main as serve_runtime

from . import BashScriptStep
from ._bootstrap import create_bindings_for

STEP_IDENTITY = "syntara.script-bash.script-bash"


def create_bindings(manifest_path: Path | None = None) -> StepBindings:
    return create_bindings_for(STEP_IDENTITY, BashScriptStep(), manifest_path)


def main() -> None:
    serve_runtime()


if __name__ == "__main__":
    main()
