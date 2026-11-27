"""The aap-workflow image lock must stay an export of uv.lock."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_REQUIREMENTS = _REPO / "aap-workflow" / "requirements.txt"


def test_aap_workflow_requirements_match_lock() -> None:
    exported = subprocess.check_output(
        [
            "uv",
            "export",
            "--package",
            "syntara-node-aap-workflow",
            "--no-dev",
            "--no-emit-project",
            "--locked",
            "--no-annotate",
            "--no-header",
        ],
        cwd=_REPO,
        text=True,
    )
    committed = "\n".join(
        line
        for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    )
    assert exported.strip() == committed.strip()
