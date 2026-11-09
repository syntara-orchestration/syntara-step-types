#!/usr/bin/env python3
"""Probe a product script-python image for AAP-95478 I7 acceptance fields."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid

import grpc
from syntara_plugin.runtime import ContainerClient, ContainerInvocation

IDENTITY = "syntara.script-python.script-python"
CODE = "import json\nprint(json.dumps({'result': 42}))"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Local image ref to run")
    parser.add_argument("--name", default="aap95478-i7-probe", help="Container name")
    parser.add_argument("--engine", default="podman", choices=("podman", "docker"))
    args = parser.parse_args()

    subprocess.run([args.engine, "rm", "-f", args.name], check=False, capture_output=True)
    subprocess.run(
        [args.engine, "run", "-d", "--name", args.name, "-p", "50051:50051", args.image],
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
        subprocess.run([args.engine, "rm", "-f", args.name], check=False, capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
