"""Script step: runs Python or Bash code in an isolated subprocess."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field
from syntara_plugin.runtime import (
    ActionStep,
    ErrorCategory,
    ExecutionContext,
    FailureCode,
    StepFailure,
)

from .executor import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CAPTURE_BYTES,
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
        le=MAX_CAPTURE_BYTES,
        description=(
            f"Maximum bytes captured per output stream (1 to {MAX_CAPTURE_BYTES}). "
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
            raise StepFailure(
                1,
                "Script execution was cancelled before starting",
                "Script execution was cancelled before starting",
                code=FailureCode.CANCELLED.value,
                category=ErrorCategory.CANCELLED,
                retryable=False,
            )

        # Enforce admission limit so Cancel and Health RPCs always have threads available.
        if not _EXECUTE_SEMAPHORE.acquire(blocking=False):
            raise StepFailure(
                1,
                "Server busy: too many concurrent executions",
                "Server busy: too many concurrent executions",
                code=FailureCode.UPSTREAM_TRANSIENT.value,
                category=ErrorCategory.DEPENDENCY,
                retryable=True,
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
            raise StepFailure(
                1,
                f"Script exited with code {exc.exit_code}",
                f"Script exited with code {exc.exit_code}",
                code=FailureCode.REMOTE_REJECTED.value,
                category=ErrorCategory.BUSINESS,
                retryable=False,
            ) from exc
        except ScriptTimeoutError as exc:
            raise StepFailure(
                1,
                "Script execution timed out",
                "Script execution timed out",
                code=FailureCode.TIMEOUT.value,
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            ) from exc
        except ScriptCancelledError as exc:
            raise StepFailure(
                1,
                "Script execution was cancelled",
                "Script execution was cancelled",
                code=FailureCode.CANCELLED.value,
                category=ErrorCategory.CANCELLED,
                retryable=False,  # Cancellation is NOT retryable
            ) from exc
        finally:
            _EXECUTE_SEMAPHORE.release()
