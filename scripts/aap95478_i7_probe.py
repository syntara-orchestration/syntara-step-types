#!/usr/bin/env python3
"""Probe a product script-python image for AAP-95478 I7 acceptance fields."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import uuid

import grpc
from syntara_plugin.runtime import ContainerClient, ContainerInvocation

IDENTITY = "syntara.script-python.script-python"
CODE = "import json\nprint(json.dumps({'result': 42}))"

# The container engine binary is resolved from constants on PATH, never from
# CLI input, so the executed command is never attacker-controlled.
ENGINE = shutil.which("podman") or shutil.which("docker")
PORT = "50051:50051"

# Reject values that could be interpreted as engine flags (e.g. "--privileged")
# or contain whitespace/shell metacharacters when passed to the engine.
SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*$")


def _validated_ref(value: str) -> str:
    """argparse type that rejects values parseable as engine flags."""
    if not SAFE_REF.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"must match {SAFE_REF.pattern!r}, got {value!r}"
        )
    return value


def main() -> int:
    if ENGINE is None:
        print("FAIL: neither podman nor docker found on PATH", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, type=_validated_ref, help="Local image ref to run"
    )
    args = parser.parse_args()

    # Container name is generated here, not accepted from CLI input, so no
    # CLI-supplied value can influence the engine command line.
    name = f"aap95478-i7-probe-{uuid.uuid4().hex[:12]}"

    # The user-supplied ref is only used as a Python-side lookup key against
    # the engine's own image listing; the ref actually passed to `run` comes
    # from the engine's stdout, so argv data never reaches the command line.
    listing = subprocess.run(
        [ENGINE, "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    local_refs = set(listing.splitlines())
    if args.image not in local_refs:
        print(f"FAIL: image {args.image!r} not present locally", file=sys.stderr)
        return 1
    image = next(ref for ref in local_refs if ref == args.image)

    subprocess.run([ENGINE, "rm", "-f", "--", name], check=False, capture_output=True)
    subprocess.run(
        [ENGINE, "run", "-d", "--name", name, "-p", PORT, "--", image],
        check=True,
    )
    try:
        time.sleep(2)
        channel = grpc.insecure_channel("127.0.0.1:50051")
        client = ContainerClient(channel)
        health = client.health()
        if IDENTITY not in health:
            print(f"FAIL: Health missing {IDENTITY}: {health}", file=sys.stderr)
            return 1
        inv = ContainerInvocation(
            invocation_id=str(uuid.uuid4()),
            workflow_run_id="aap95478-i7",
            step_revision_id=IDENTITY,
            inputs={"code": CODE, "environment": {}},
            runtime_context={},
            timeout_seconds=30,
            credential_file_paths={},
        )
        terminal = list(client.stream(inv))[-1]
        if terminal.status_code != 0:
            print(f"FAIL: status_code={terminal.status_code} {terminal}", file=sys.stderr)
            return 1
        output = terminal.output
        for key in ("return_code", "stdout", "stderr"):
            if key not in output:
                print(f"FAIL: missing business field {key}: {output}", file=sys.stderr)
                return 1
        for banned in ("statusCode", "errorResult"):
            if banned in output:
                print(f"FAIL: wrapper key leaked into business output: {banned}", file=sys.stderr)
                return 1
        print("OK", {"identity": IDENTITY, "output": output})
        return 0
    finally:
        subprocess.run([ENGINE, "rm", "-f", "--", name], check=False, capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
