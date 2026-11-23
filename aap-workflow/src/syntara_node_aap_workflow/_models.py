"""Pydantic models for the AAP Workflow step.

The input surface preserves the in-process adapter's author-facing schema
(aap_workflow_job_template.schema.json) minus the AO-only identifiers
(``integration_id`` / ``credential_id``), which AO resolves into the integration
endpoint and the declared credential before the container is invoked.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AapWorkflowInput(BaseModel):
    """Launch inputs for an AAP workflow job template."""

    model_config = ConfigDict(extra="forbid")

    workflow_job_template_id: int | None = Field(default=None, ge=1)
    workflow_job_template_name: str | None = Field(default=None, min_length=1)
    organization_id: int | None = Field(default=None, ge=1)
    organization_name: str | None = Field(default=None, min_length=1)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    limit: str | None = None
    scm_branch: str | None = None
    tags: str | None = None
    skip_tags: str | None = None
    labels: list[str] = Field(default_factory=list)
    inventory_id: int | None = Field(default=None, ge=1)
    inventory_name: str | None = Field(default=None, min_length=1)
    credential_name: str | None = None
    timeout: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_template_and_inventory(self) -> AapWorkflowInput:
        if self.workflow_job_template_id is None:
            if self.workflow_job_template_name is None:
                raise ValueError(
                    "workflow_job_template_id or workflow_job_template_name is required."
                )
            if self.organization_name is None:
                raise ValueError(
                    "workflow_job_template_name requires organization_name "
                    "(or use workflow_job_template_id)."
                )
        if self.inventory_name is not None and self.organization_name is None:
            # Name-based inventory override requires organization, matching the
            # adapter schema's conditional.
            raise ValueError("inventory_name requires organization_name.")
        return self


class AapWorkflowOutput(BaseModel):
    """Observed result of a launched AAP workflow job.

    Preserves the adapter's result fields. The failure branch of the adapter's
    discriminated union additionally carries ``error``; the container returns
    the same fields on failure so AO can project today's result schema
    (final mapping is settled in AAP-98908).
    """

    model_config = ConfigDict(extra="forbid")

    workflow_job_id: int | None = None
    workflow_job_url: str | None = None
    workflow_job_status: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    created: str | None = None
    started: str | None = None
    finished: str | None = None
    error: str | None = None
