"""Unit tests for the AAP Workflow step scaffold."""

from __future__ import annotations

import pytest
from syntara_node_aap_workflow import AapWorkflowInput, AapWorkflowOutput, AapWorkflowStep
from syntara_plugin.runtime import (
    ContainerInvocationError,
    ExecutionContext,
    FailureCode,
)


class TestAapWorkflowInput:
    def test_template_id_alone_is_valid(self) -> None:
        model = AapWorkflowInput(workflow_job_template_id=42)
        assert model.workflow_job_template_id == 42

    def test_template_name_requires_organization(self) -> None:
        with pytest.raises(ValueError, match="organization_name"):
            AapWorkflowInput(workflow_job_template_name="example-workflow")

    def test_template_name_with_organization_is_valid(self) -> None:
        model = AapWorkflowInput(
            workflow_job_template_name="example-workflow", organization_name="Default"
        )
        assert model.organization_name == "Default"

    def test_template_reference_is_required(self) -> None:
        with pytest.raises(ValueError, match="workflow_job_template"):
            AapWorkflowInput()

    def test_inventory_name_requires_organization(self) -> None:
        with pytest.raises(ValueError, match="inventory_name requires organization_name"):
            AapWorkflowInput(
                workflow_job_template_id=42,
                inventory_name="production",
            )

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValueError):
            AapWorkflowInput(workflow_job_template_id=42, integration_id="nope")  # type: ignore[call-arg]


class TestAapWorkflowOutput:
    def test_defaults_preserve_result_shape(self) -> None:
        model = AapWorkflowOutput(workflow_job_id=7)
        assert model.workflow_job_status is None
        assert model.artifacts == {}
        assert model.error is None


class TestAapWorkflowStep:
    def test_run_is_a_deliberate_not_implemented_failure(self) -> None:
        step = AapWorkflowStep()
        with pytest.raises(ContainerInvocationError) as exc_info:
            step.run(
                AapWorkflowInput(workflow_job_template_id=42),
                ExecutionContext(invocation_id="test-invocation-1"),
            )
        assert exc_info.value.code == FailureCode.REMOTE_REJECTED
        assert "not implemented" in exc_info.value.safe_message
