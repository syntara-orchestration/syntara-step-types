"""AAP Workflow step: launches and observes an AAP workflow job template from a container."""

from __future__ import annotations

import threading

from syntara_plugin.runtime import (
    ActionStep,
    ContainerInvocationError,
    CredentialAccessError,
    ErrorCategory,
    ExecutionContext,
    FailureCode,
    StepFailure,
)

from ._auth import AAP_CREDENTIAL_NAME, resolve_aap_connection, validate_aap_integration
from ._models import AapWorkflowInput, AapWorkflowOutput
from .executor import execute_aap_workflow

__all__ = [
    "AapWorkflowInput",
    "AapWorkflowOutput",
    "AapWorkflowStep",
]

# Hard admission limit: reserve 2 out of 10 gRPC threads for Cancel and Health RPCs.
_EXECUTE_SEMAPHORE = threading.Semaphore(8)

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


class AapWorkflowStep(ActionStep[AapWorkflowInput, AapWorkflowOutput]):
    """Launch an AAP workflow job template and observe it to a terminal status.

    The integration endpoint, TLS material, and credential arrive through the
    SDK runtime (runtime_context + the declared ``syntara.aap.credential``),
    never as author input.
    """

    def run(self, inputs: AapWorkflowInput, context: ExecutionContext) -> AapWorkflowOutput:
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
            try:
                # Validate the integration destination before touching credentials,
                # mirroring the HTTP step's guard ordering.
                integration = validate_aap_integration(context.runtime_context)
                try:
                    credential_doc = context.credential(AAP_CREDENTIAL_NAME).read()
                except CredentialAccessError as exc:
                    raise ContainerInvocationError(
                        FailureCode.POLICY_DENIED,
                        "The configured AAP credential is unavailable.",
                        retryable=False,
                        outcome="not_started",
                    ) from exc
                connection = resolve_aap_connection(integration, credential_doc)
                return execute_aap_workflow(inputs, connection, context.cancellation)
            except ContainerInvocationError as exc:
                raise _to_step_failure(exc) from exc
        finally:
            _EXECUTE_SEMAPHORE.release()
