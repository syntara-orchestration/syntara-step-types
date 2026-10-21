"""Async subprocess execution for the script step."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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

# SDK permits 1 MiB for entire serialized result. Allow ~400KB per stream to account for:
# - JSON encoding overhead (quotes, escaping)
# - Result envelope (field names, return_code, etc.)
# - stdout_json field which may duplicate stdout content
DEFAULT_MAX_OUTPUT_BYTES = 409_600  # 400 KB per stream
MAX_ENV_VAR_LENGTH = 32768
SCRIPT_CLEANUP_TERMINATE_TIMEOUT = 1.0
SCRIPT_CLEANUP_KILL_TIMEOUT = 0.5


class ScriptExecutionError(Exception):
    """Raised when the script subprocess exits with a non-zero code."""

    def __init__(self, message: str, exit_code: int, stdout: str, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class ScriptTimeoutError(Exception):
    """Raised when script execution exceeds the allowed timeout."""


async def _cleanup_process(process: asyncio.subprocess.Process, process_group_id: int) -> None:
    """Clean up process and all descendants in its process group."""
    # First, try to terminate the direct process if still running
    if process.returncode is None:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), timeout=SCRIPT_CLEANUP_TERMINATE_TIMEOUT)
        except TimeoutError:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
                await asyncio.wait_for(process.wait(), timeout=SCRIPT_CLEANUP_KILL_TIMEOUT)
            except (TimeoutError, ProcessLookupError):
                pass
        except ProcessLookupError:
            pass

    # Always attempt to clean up any remaining process group members
    # This catches background processes that survive after the leader exits
    try:
        os.killpg(process_group_id, signal.SIGTERM)
        await asyncio.sleep(SCRIPT_CLEANUP_TERMINATE_TIMEOUT)
    except ProcessLookupError:
        pass  # Process group already gone

    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass  # Process group already gone

    if process.stdin and not process.stdin.is_closing():
        process.stdin.close()
        with contextlib.suppress(Exception):
            await process.stdin.wait_closed()

    if hasattr(process, "_transport") and process._transport is not None:  # noqa: SLF001
        with contextlib.suppress(Exception):
            process._transport.close()  # noqa: SLF001


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


async def _communicate_limited(
    process: asyncio.subprocess.Process,
    max_output_bytes: int,
) -> tuple[bytes, bytes, bool, bool]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("subprocess created without PIPE for stdout/stderr")
    (stdout_bytes, stdout_truncated), (stderr_bytes, stderr_truncated) = await asyncio.gather(
        _read_stream_limited(process.stdout, max_output_bytes),
        _read_stream_limited(process.stderr, max_output_bytes),
    )
    await process.wait()
    return stdout_bytes, stderr_bytes, stdout_truncated, stderr_truncated


def _get_cgroup_memory_limit() -> int | None:
    for cgroup_path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(cgroup_path).read_text().strip()
            if value == "max":
                return None
            return int(value)
        except (ValueError, OSError):
            continue
    return None


def _create_python_launcher_with_limit(user_code: str, max_bytes: int) -> str:
    """Create a Python launcher that applies resource limits before executing user code.

    This preserves the user code's module semantics (e.g., __future__ imports).
    """
    return f"""import resource
import sys

# Apply memory limit
resource.setrlimit(resource.RLIMIT_AS, ({max_bytes}, {max_bytes}))

# Execute user code
exec(compile({user_code!r}, '<script>', 'exec'))
"""


def _sanitize_env_value(value: object) -> str:
    str_value = json.dumps(value) if isinstance(value, dict | list) else str(value)
    if "\0" in str_value:
        raise ValueError("Environment variable values cannot contain null bytes")
    if len(str_value) > MAX_ENV_VAR_LENGTH:
        raise ValueError(
            f"Environment variable value exceeds maximum length ({MAX_ENV_VAR_LENGTH} bytes)"
        )
    return str_value


def _prepare_script_env(environment: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_ALLOWLIST}
    if environment:
        for key, value in environment.items():
            env[key] = _sanitize_env_value(value)
    return env


async def _execute_script_common(
    command: list[str],
    environment: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    env = _prepare_script_env(environment)
    process = None
    process_group_id = None

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        process_group_id = process.pid

        stdout_bytes, stderr_bytes, stdout_truncated, stderr_truncated = await asyncio.wait_for(
            _communicate_limited(process, max_output_bytes),
            timeout=timeout_seconds,
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if stdout_truncated or stderr_truncated:
            notice = (
                f"\n[Output truncated: exceeded {max_output_bytes} byte limit"
                f" (stdout: {'truncated' if stdout_truncated else 'complete'}"
                f", stderr: {'truncated' if stderr_truncated else 'complete'})]"
            )
            stderr = stderr + notice

        returncode = process.returncode
        if returncode is None:
            raise RuntimeError("Process returncode is None after communicate()")

        if returncode != 0:
            error_msg = f"Script failed with exit code {returncode}"
            if stderr:
                error_msg += f": {stderr.strip()}"
            raise ScriptExecutionError(
                message=error_msg, exit_code=returncode, stdout=stdout, stderr=stderr
            )

        return {"stdout": stdout, "stderr": stderr, "return_code": returncode}

    except (ScriptExecutionError, RuntimeError, ValueError):
        raise
    except TimeoutError as exc:
        raise ScriptTimeoutError("Script execution timed out") from exc
    except subprocess.SubprocessError as exc:
        raise ScriptExecutionError(
            message=f"Subprocess error: {exc}", exit_code=-1, stdout="", stderr=str(exc)
        ) from exc
    except Exception as exc:
        raise ScriptExecutionError(
            message=f"Unexpected error executing script: {exc}",
            exit_code=-1,
            stdout="",
            stderr=str(exc),
        ) from exc
    finally:
        if process and process_group_id:
            await _cleanup_process(process, process_group_id)


async def run_script_async(
    language: str,
    code: str,
    environment: dict[str, str],
) -> dict[str, Any]:
    """Execute a script and return a raw result dict.

    Returns keys: return_code, stdout, stderr, stdout_json.
    Raises ScriptExecutionError or ScriptTimeoutError on failure.
    """
    cgroup_limit = _get_cgroup_memory_limit()

    if language == "bash":
        # For bash, prepend ulimit command
        if cgroup_limit:
            ulimit_kb = int(cgroup_limit * 0.75 / 1024)
            code = f"ulimit -v {ulimit_kb}\n{code}"
        command = ["bash", "-c", code]
    else:
        # For Python, use launcher approach to preserve module semantics
        if cgroup_limit:
            code = _create_python_launcher_with_limit(code, int(cgroup_limit * 0.75))
        command = [sys.executable, "-c", code]

    result = await _execute_script_common(command, environment)

    stdout_json = None
    if language == "python" and result["stdout"].strip():
        with contextlib.suppress(json.JSONDecodeError):
            stdout_json = json.loads(result["stdout"])
        if stdout_json is None:
            lines = [line for line in result["stdout"].strip().split("\n") if line.strip()]
            if lines:
                with contextlib.suppress(json.JSONDecodeError):
                    stdout_json = json.loads(lines[-1])

    return {**result, "stdout_json": stdout_json}
