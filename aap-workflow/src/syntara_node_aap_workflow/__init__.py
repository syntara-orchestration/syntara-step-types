"""AAP Workflow step: launches and observes an AAP workflow job template from a container."""

from __future__ import annotations

from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    ErrorCategory,
    ExecutionContext,
    FailureCode,
    StepFailure,
)

from ._models import AapWorkflowInput, AapWorkflowOutput

__all__ = [
    "AapWorkflowInput",
    "AapWorkflowOutput",
    "AapWorkflowStep",
]

# Hard admission limit: reserve 2 out of 10 gRPC threads for Cancel and Health RPCs.
_EXECUTE_SEMAPHORE_LIMIT = 8

_ERROR_CATEGORIES = {
    FailureCode.AUTHENTICATION_FAILED: ErrorCategory.DEPENDENCY,
    FailureCode.AUTHORIZATION_FAILED: ErrorCategory.DEPENDENCY,
    FailureCode.NOT_FOUND: ErrorCategory.BUSINESS,
    FailureCode.REMOTE_REJECTED: ErrorCategory.BUSINESS,
    FailureCode.UPSTREAM_TRANSIENT: ErrorCategory.DEPENDENCY,
    FailureCode.TIMEOUT: ErrorCategory.TIMEOUT,
    FailureCode.OUTCOME_UNKNOWN: ErrorCategory.DEPENDENCY,
    FailureCode.POLICY_DENIED: ErrorCategory.POLICY,
    FailureCode.CANCELLED: ErrorCategory.CANCELLED,
}


class AapWorkflowStep(ActionStep[AapWorkflowInput, AapWorkflowOutput]):
    """Launch an AAP workflow job template and observe it to a terminal status.

    Scaffold state (plan Step 1): input/output contracts and failure mapping are
    in place; the launch/observe executor lands with plan Step 4.
    """

    def run(self, inputs: AapWorkflowInput, context: ExecutionContext) -> AapWorkflowOutput:
        raise ContainerInvocationError(
            FailureCode.REMOTE_REJECTED,
            "AAP Workflow execution is not implemented yet (plan Step 4).",
            retryable=False,
            outcome="not_started",
        )


def _to_step_failure(exc: ContainerInvocationError) -> StepFailure:
    """Map a ContainerInvocationError onto the SDK StepFailure surface."""
    code = exc.code.value if isinstance(exc.code, FailureCode) else str(exc.code)
    category = _ERROR_CATEGORIES.get(exc.code, ErrorCategory.INTERNAL)
    return StepFailure(
        1,
        exc.safe_message,
        exc.safe_message,
        code=code,
        category=category,
        retryable=exc.retryable,
    )
