"""Script step: runs Python or Bash code in an isolated subprocess."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field
from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    ExecutionContext,
    FailureCode,
)

from .executor import (
    DEFAULT_MAX_OUTPUT_BYTES,
    ScriptCancelledError,
    ScriptExecutionError,
    ScriptTimeoutError,
    run_script_async,
)

# Hard admission limit: reserve 2 out of 10 gRPC threads for Cancel and Health RPCs.
# Without this, 10 simultaneous Execute calls starve all control-plane operations.
_EXECUTE_SEMAPHORE = threading.Semaphore(8)


class ScriptInput(BaseModel):
    language: Literal["python", "bash"] = "python"
    code: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    max_output_bytes: int = Field(
        default=DEFAULT_MAX_OUTPUT_BYTES,
        gt=0,
        description=(
            "Maximum bytes captured per output stream (stdout and stderr). "
            "Output exceeding this limit is truncated. "
            f"Defaults to {DEFAULT_MAX_OUTPUT_BYTES} bytes (~270 KB per stream). "
            "The combined serialized result must not exceed the 1 MiB SDK ABI limit."
        ),
    )


class ScriptOutput(BaseModel):
    return_code: int
    stdout: str
    stderr: str
    stdout_json: Any | None = None


class ScriptStep(ActionStep[ScriptInput, ScriptOutput]):
    def run(self, inputs: ScriptInput, context: ExecutionContext) -> ScriptOutput:
        # Check if already cancelled before starting
        if context.cancelled:
            raise ContainerInvocationError(
                FailureCode.CANCELLED,
                "Script execution was cancelled before starting",
                retryable=False,
                outcome="not_started",
            )

        # Enforce admission limit so Cancel and Health RPCs always have threads available.
        if not _EXECUTE_SEMAPHORE.acquire(blocking=False):
            raise ContainerInvocationError(
                FailureCode.UPSTREAM_TRANSIENT,
                "Server busy: too many concurrent executions",
                retryable=True,
                outcome="not_started",
            )
        try:
            result = asyncio.run(
                run_script_async(
                    inputs.language,
                    inputs.code,
                    dict(inputs.environment),
                    max_output_bytes=inputs.max_output_bytes,
                    cancellation=context.cancellation,
                )
            )
            return ScriptOutput(**result)
        except ScriptExecutionError as exc:
            raise ContainerInvocationError(
                FailureCode.REMOTE_REJECTED,
                f"Script exited with code {exc.exit_code}",
                retryable=False,
                outcome="not_completed",
            ) from exc
        except ScriptTimeoutError as exc:
            raise ContainerInvocationError(
                FailureCode.TIMEOUT,
                "Script execution timed out",
                retryable=True,
                outcome="not_completed",
            ) from exc
        except ScriptCancelledError as exc:
            raise ContainerInvocationError(
                FailureCode.CANCELLED,
                "Script execution was cancelled",
                retryable=False,  # Cancellation is NOT retryable
                outcome="not_completed",
            ) from exc
        finally:
            _EXECUTE_SEMAPHORE.release()
