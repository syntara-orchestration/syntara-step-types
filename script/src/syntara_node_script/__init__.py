"""Script step: runs Python or Bash code in an isolated subprocess."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field
from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    ExecutionContext,
    FailureCode,
)

from .executor import (
    ScriptCancelledError,
    ScriptExecutionError,
    ScriptTimeoutError,
    run_script_async,
)


class ScriptInput(BaseModel):
    language: Literal["python", "bash"] = "python"
    code: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)


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

        try:
            result = asyncio.run(
                run_script_async(
                    inputs.language,
                    inputs.code,
                    dict(inputs.environment),
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
