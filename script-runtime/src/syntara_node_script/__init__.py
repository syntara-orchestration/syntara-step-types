"""Script step: runs Python or Bash code in an isolated subprocess."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
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
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    return_code: int
    stdout: str
    stderr: str


class PythonScriptOutput(ScriptOutput):
    stdout_json: Any | None = None


class BashScriptOutput(ScriptOutput):
    pass


def _run_script[OutputT: ScriptOutput](
    language: Literal["python", "bash"],
    inputs: ScriptInput,
    context: ExecutionContext,
    output_type: type[OutputT],
) -> OutputT:
    if context.cancelled:
        raise StepFailure(
            1,
            "Script execution was cancelled before starting",
            "Script execution was cancelled before starting",
            code=FailureCode.CANCELLED.value,
            category=ErrorCategory.CANCELLED,
            retryable=False,
        )

    # Reserve enough gRPC workers for Cancel and Health while scripts run.
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
                language,
                inputs.code,
                dict(inputs.environment),
                max_output_bytes=inputs.max_output_bytes,
                cancellation=context.cancellation,
            )
        )
        return output_type(**result)
    except ScriptExecutionError as exc:
        message = f"Script exited with code {exc.exit_code}"
        raise StepFailure(
            1,
            message,
            message,
            code=FailureCode.REMOTE_REJECTED.value,
            category=ErrorCategory.BUSINESS,
            retryable=False,
        ) from exc
    except ScriptTimeoutError as exc:
        message = "Script execution timed out"
        raise StepFailure(
            1,
            message,
            message,
            code=FailureCode.TIMEOUT.value,
            category=ErrorCategory.TIMEOUT,
            retryable=True,
        ) from exc
    except ScriptCancelledError as exc:
        message = "Script execution was cancelled"
        raise StepFailure(
            1,
            message,
            message,
            code=FailureCode.CANCELLED.value,
            category=ErrorCategory.CANCELLED,
            retryable=False,
        ) from exc
    finally:
        _EXECUTE_SEMAPHORE.release()


class PythonScriptStep(ActionStep[ScriptInput, PythonScriptOutput]):
    def run(self, inputs: ScriptInput, context: ExecutionContext) -> PythonScriptOutput:
        return _run_script("python", inputs, context, PythonScriptOutput)


class BashScriptStep(ActionStep[ScriptInput, BashScriptOutput]):
    def run(self, inputs: ScriptInput, context: ExecutionContext) -> BashScriptOutput:
        return _run_script("bash", inputs, context, BashScriptOutput)


__all__ = [
    "BashScriptOutput",
    "BashScriptStep",
    "PythonScriptOutput",
    "PythonScriptStep",
    "ScriptInput",
    "ScriptOutput",
]
