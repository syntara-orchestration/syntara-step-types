"""AAP job template step: launch and monitor a Controller job."""

from __future__ import annotations

import threading

from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    ErrorCategory,
    ExecutionContext,
    FailureCode,
    StepFailure,
)

from ._models import AAPJobTemplateInput, AAPJobTemplateOutput
from .executor import (
    AAPCancelledError,
    AAPExecutionError,
    AAPPolicyError,
    AAPTimeoutError,
    execute_aap_job_template,
)

_AAP_CREDENTIAL_ID = "syntara.aap.auth"
# Keep Execute RPC capacity available for Health and Cancel control calls.
_EXECUTE_SEMAPHORE = threading.Semaphore(8)


class AAPJobTemplateStep(ActionStep[AAPJobTemplateInput, AAPJobTemplateOutput]):
    """Launch an AAP job template and wait for its terminal result."""

    def run(self, inputs: AAPJobTemplateInput, context: ExecutionContext) -> AAPJobTemplateOutput:
        if not _EXECUTE_SEMAPHORE.acquire(blocking=False):
            raise StepFailure(
                1,
                "Server busy: too many concurrent AAP executions",
                "Server busy: too many concurrent AAP executions",
                code=FailureCode.UPSTREAM_TRANSIENT.value,
                category=ErrorCategory.DEPENDENCY,
                retryable=True,
            )
        try:
            try:
                if context.cancelled:
                    raise AAPCancelledError("AAP job execution was cancelled before starting.")
                credential_value = context.credential(_AAP_CREDENTIAL_ID).read()
                if not isinstance(credential_value, dict):
                    raise AAPPolicyError("The configured AAP credential is invalid.")
                return execute_aap_job_template(
                    inputs,
                    context.runtime_context,
                    credential_value,
                    context.cancellation,
                )
            except ContainerInvocationError:
                raise
            except AAPExecutionError as exc:
                if isinstance(exc, AAPCancelledError):
                    code, category, retryable = (
                        FailureCode.CANCELLED,
                        ErrorCategory.CANCELLED,
                        False,
                    )
                elif isinstance(exc, AAPTimeoutError):
                    code, category, retryable = FailureCode.TIMEOUT, ErrorCategory.TIMEOUT, False
                elif isinstance(exc, AAPPolicyError):
                    code, category, retryable = (
                        FailureCode.POLICY_DENIED,
                        ErrorCategory.POLICY,
                        False,
                    )
                elif exc.retryable:
                    code, category, retryable = (
                        FailureCode.UPSTREAM_TRANSIENT,
                        ErrorCategory.DEPENDENCY,
                        True,
                    )
                else:
                    code, category, retryable = (
                        FailureCode.REMOTE_REJECTED,
                        ErrorCategory.BUSINESS,
                        False,
                    )
                raise StepFailure(
                    1,
                    str(exc),
                    str(exc),
                    code=code.value,
                    category=category,
                    retryable=retryable,
                ) from exc
        finally:
            _EXECUTE_SEMAPHORE.release()


__all__ = ["AAPJobTemplateInput", "AAPJobTemplateOutput", "AAPJobTemplateStep"]
