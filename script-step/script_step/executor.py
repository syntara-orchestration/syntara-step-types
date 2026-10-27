"""Isolated subprocess execution shared by the Python and Bash script steps.

Ported from the previous-generation ``syntara_node_script`` executor. The
environment allowlist, per-stream output cap, and process-group cleanup are
kept. The cgroup memory-limit prepend is dropped for this PoC. Invocation
timeouts are enforced by the container runtime server; when the server
cancels an invocation, the cancellation callback kills the process group.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from typing import Any, Callable

SAFE_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
    }
)

DEFAULT_MAX_OUTPUT_BYTES = 1_048_576  # 1 MB per stream
MAX_ENV_VAR_LENGTH = 32768
CLEANUP_TERMINATE_TIMEOUT = 1.0
CLEANUP_KILL_TIMEOUT = 0.5
_CANCEL_POLL_SECONDS = 0.1


class ScriptExecutionError(Exception):
    """The script subprocess exited with a non-zero code."""

    def __init__(self, message: str, exit_code: int, stdout: str, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class ScriptCancelledError(Exception):
    """The invocation was cancelled before the script finished."""


def _sanitize_env_value(value: object) -> str:
    str_value = json.dumps(value) if isinstance(value, dict | list) else str(value)
    if "\0" in str_value:
        raise ValueError("Environment variable values cannot contain null bytes")
    if len(str_value) > MAX_ENV_VAR_LENGTH:
        raise ValueError(f"Environment variable value exceeds maximum length ({MAX_ENV_VAR_LENGTH} bytes)")
    return str_value


def _prepare_script_env(environment: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in SAFE_ENV_ALLOWLIST}
    if environment:
        for key, value in environment.items():
            env[key] = _sanitize_env_value(value)
    return env


async def _read_stream_limited(
    stream: asyncio.StreamReader,
    max_bytes: int,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total_buffered = 0
    truncated = False

    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        if not truncated:
            remaining = max_bytes - total_buffered
            if len(chunk) <= remaining:
                chunks.append(chunk)
                total_buffered += len(chunk)
            else:
                chunks.append(chunk[:remaining])
                total_buffered += remaining
                truncated = True

    return b"".join(chunks), truncated


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate the script's whole process group, escalating to SIGKILL."""
    if process.returncode is not None or process.pid is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=CLEANUP_TERMINATE_TIMEOUT)
    except TimeoutError:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=CLEANUP_KILL_TIMEOUT)
    except ProcessLookupError:
        pass
    if process.stdin and not process.stdin.is_closing():
        process.stdin.close()


async def _watch_cancellation(
    process: asyncio.subprocess.Process,
    cancelled: Callable[[], bool],
) -> None:
    while not cancelled():
        await asyncio.sleep(_CANCEL_POLL_SECONDS)
        if process.returncode is not None:
            return
    await _terminate_process_group(process)


async def run_script_async(
    language: str,
    code: str,
    environment: dict[str, str],
    cancelled: Callable[[], bool] | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Execute one script and return ``{stdout, stderr, return_code}``.

    Raises ScriptCancelledError when the caller reports cancellation and
    ScriptExecutionError when the script exits non-zero.
    """
    command = ["/bin/bash", "-c", code] if language == "bash" else [sys.executable, "-c", code]
    env = _prepare_script_env(environment)
    process: asyncio.subprocess.Process | None = None
    watcher: asyncio.Task[None] | None = None

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        if cancelled is not None:
            watcher = asyncio.create_task(_watch_cancellation(process, cancelled))

        assert process.stdout is not None and process.stderr is not None
        (stdout_bytes, stdout_truncated), (stderr_bytes, stderr_truncated) = await asyncio.gather(
            _read_stream_limited(process.stdout, max_output_bytes),
            _read_stream_limited(process.stderr, max_output_bytes),
        )
        await process.wait()

        if cancelled is not None and cancelled():
            raise ScriptCancelledError("Script execution was cancelled")

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if stdout_truncated or stderr_truncated:
            stderr += (
                f"\n[Output truncated: exceeded {max_output_bytes} byte limit"
                f" (stdout: {'truncated' if stdout_truncated else 'complete'}"
                f", stderr: {'truncated' if stderr_truncated else 'complete'})]"
            )

        returncode = process.returncode
        if returncode is None:
            raise RuntimeError("Process returncode is None after wait()")

        if returncode != 0:
            error_msg = f"Script failed with exit code {returncode}"
            if stderr:
                error_msg += f": {stderr.strip()}"
            raise ScriptExecutionError(message=error_msg, exit_code=returncode, stdout=stdout, stderr=stderr)

        return {"stdout": stdout, "stderr": stderr, "return_code": returncode}

    except (ScriptCancelledError, ScriptExecutionError, RuntimeError, ValueError):
        raise
    except subprocess.SubprocessError as exc:
        raise ScriptExecutionError(
            message=f"Subprocess error: {exc}", exit_code=-1, stdout="", stderr=str(exc)
        ) from exc
    finally:
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        if process:
            await _terminate_process_group(process)


def parse_stdout_json(language: str, stdout: str) -> Any | None:
    """Best-effort JSON parse of stdout (Python scripts only)."""
    if language != "python" or not stdout.strip():
        return None
    with suppress(json.JSONDecodeError):
        return json.loads(stdout)
    lines = [line for line in stdout.strip().split("\n") if line.strip()]
    if lines:
        with suppress(json.JSONDecodeError):
            return json.loads(lines[-1])
    return None
