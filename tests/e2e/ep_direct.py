"""Direct Execution Plane dispatch for the plugin e2e tests.

The plugin images advertise their own namespaced step identities and speak
their native input contract, so they are exercised by submitting work items
straight to the Execution Plane — not through AO's script node (which
requests the legacy script.v1 identity served by the EP test fixture).

Submission goes through the Execution Plane checkout's own
``tools.submit_work_item`` (``--payload-file`` carries the plugin's native
payload), so the envelope and auth logic stay with their owner. The tool
prints the terminal work item as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

EP_TOOLS_DIR = "EP_TOOLS_DIR"


def _submit_tool() -> list[str]:
    tools_dir = Path(os.environ[EP_TOOLS_DIR])
    return [
        "uv",
        "run",
        "--directory",
        str(tools_dir),
        "python",
        "-m",
        "tools.submit_work_item",
    ]


def dispatch_plugin_workload(
    *,
    step_revision_id: str,
    image: str,
    inputs: dict[str, Any],
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Submit one work item and return the terminal work item as a dict."""

    payload = {
        "invocation": {
            "workflow_run_id": str(uuid.uuid4()),
            "step_revision_id": step_revision_id,
            "inputs": inputs,
            "timeout_seconds": timeout_seconds,
        },
        "image": image,
        "selectors": {},
        "output_config": {"stdout": "stdout", "stderr": "stderr", "exit_code": "exit_code"},
    }
    payload_file = (
        Path(os.environ.get("TMPDIR", "/tmp")) / f"step-types-e2e-{uuid.uuid4().hex}.json"
    )
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                *_submit_tool(),
                "--payload-file",
                str(payload_file),
                "--wait",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 120,
            check=False,
        )
    finally:
        payload_file.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"submit_work_item failed ({result.returncode}): {result.stderr.strip()}"
        )

    # The tool logs [INFO] lines before the final JSON document.
    lines = result.stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    item = json.loads("\n".join(lines[start:]))
    if not isinstance(item, dict):
        message = f"unexpected work item payload: {item!r}"
        raise TypeError(message)
    return item
