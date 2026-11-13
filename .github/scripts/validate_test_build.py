"""Validate the SDK build inventory before integration tests start."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "support"))

from step_type_artifacts import load_inventory  # noqa: E402


def main() -> int:
    try:
        inventory = load_inventory()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"test-integration prerequisites failed: {error}", file=sys.stderr)
        return 1
    build_id = inventory.get("buildId", "unknown")
    platform = inventory.get("platform", "unknown")
    print(f"Using SDK build {build_id} ({platform}) from .test-artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
