"""Container entrypoint for the Python script plugin."""

from __future__ import annotations

from pathlib import Path

from syntara_plugin.runtime import StepBindings

from . import PythonScriptStep
from ._bootstrap import create_bindings_for, serve

STEP_IDENTITY = "syntara.script-python.script-python"


def create_bindings(manifest_path: Path | None = None) -> StepBindings:
    return create_bindings_for(STEP_IDENTITY, PythonScriptStep(), manifest_path)


def main() -> None:
    serve(STEP_IDENTITY, create_bindings())


if __name__ == "__main__":
    main()
