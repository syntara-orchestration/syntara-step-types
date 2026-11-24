"""Typed inputs and outputs for the AAP job template step."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AAPJobTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_template_id: int | None = Field(default=None, ge=1)
    job_template_name: str | None = None
    organization_id: int | None = Field(default=None, ge=1, alias="organizationId")
    organization_name: str | None = None
    inventory_id: int | None = Field(default=None, ge=1)
    inventory_name: str | None = None
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    limit: str | None = None
    tags: str | None = None
    skip_tags: str | None = None
    labels: list[str] | None = Field(default=None, max_length=100)
    job_credentials: list[int] | None = None
    credential_names: list[str] | None = Field(default=None, alias="credentialNames")
    verbosity: int = Field(default=0, ge=0, le=5)
    job_type: Literal["run", "check"] | None = None
    forks: int | None = Field(default=None, ge=0)
    job_slicing: int | None = Field(default=None, ge=1)
    diff_mode: bool | None = None
    execution_environment: str | None = None
    instance_group_id: int | None = Field(default=None, ge=1)
    instance_group_name: str | None = None
    timeout_seconds: int = Field(default=3600, ge=1, le=86400)

    @model_validator(mode="after")
    def validate_resource_references(self) -> AAPJobTemplateInput:
        if self.job_template_id is None and not self.job_template_name:
            raise ValueError("Either job_template_id or job_template_name must be provided")
        if self.job_template_id is None and self.job_template_name and not self.organization_name:
            raise ValueError("organization_name is required when using job_template_name")
        if self.inventory_id is None and self.inventory_name and not self.organization_name:
            raise ValueError("organization_name is required when using inventory_name")
        if (
            self.instance_group_id is None
            and self.instance_group_name
            and not self.organization_name
        ):
            raise ValueError("organization_name is required when using instance_group_name")
        if self.labels and self.organization_id is None and not self.organization_name:
            raise ValueError("organization_id or organization_name is required when using labels")
        return self


class AAPJobTemplateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: int
    job_url: str
    job_status: str
    artifacts: dict[str, Any]
    created: str
    started: str
    finished: str
