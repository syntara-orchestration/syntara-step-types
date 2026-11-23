"""Pydantic models for the AAP Workflow step.

The input surface preserves the in-process adapter's author-facing schema
(``aap_workflow_job_template.schema.json``) minus the AO-only identifiers
(``integration_id`` / ``credential_id``), which AO resolves into the integration
endpoint and the declared credential before the container is invoked.

The output preserves the adapter's discriminated result union on ``status``
(Completed/Failed) in a single model: branch-specific required fields are
enforced by a model validator so the serialized result matches the adapter's
``oneOf`` schema exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


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

    Preserves the adapter's discriminated result union on ``status``:

    - ``completed`` requires ``workflow_job_id``, ``created``, ``started``,
      and ``finished`` (the adapter's Completed Result branch).
    - ``failed`` requires ``error`` and ``workflow_job_id``; every other field
      may be null (the adapter's Failed Result branch).
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed"]
    workflow_job_id: int | None = None
    workflow_job_url: str | None = None
    workflow_job_status: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    created: str | None = None
    started: str | None = None
    finished: str | None = None
    error: str | None = None

    @model_serializer(mode="wrap")
    def _drop_none(self, handler: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        """Omit null fields, matching the adapter's serialized result shape.

        The manifest output is the adapter's discriminated union with
        ``additionalProperties: false`` per branch; pydantic's default dump
        would emit ``"error": null`` on the completed branch (and similar
        nulls on the failed branch), which the union rejects.
        """
        return {key: value for key, value in handler(self).items() if value is not None}

    @model_validator(mode="after")
    def _validate_union_branch(self) -> AapWorkflowOutput:
        if self.status == "completed":
            missing = [
                name
                for name in ("workflow_job_id", "created", "started", "finished")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    f"completed result requires non-null values for: {', '.join(missing)}."
                )
        elif self.error is None or self.workflow_job_status is None:
            raise ValueError("failed result requires error and workflow_job_status.")
        return self
