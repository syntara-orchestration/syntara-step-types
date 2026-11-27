"""Typed inputs and output for the isolated AO task agent Action."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AOTaskAgentInput(BaseModel):
    """Prepared conversation and host-authorized operation descriptions."""

    contract_version: int = Field(ge=1, le=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    max_model_calls: int = Field(default=8, ge=1, le=32)


class AOTaskAgentOutput(BaseModel):
    """Final response returned to AO for invocation persistence."""

    contract_version: int = Field(default=1, ge=1, le=1)
    content: str | dict[str, Any]
    last_turn_index: int = Field(ge=1)
    total_duration_ms: int = Field(ge=0)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    structured_output_metadata: dict[str, Any] | None = None
    tools_used: list[str] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    llm_token_usage_log: list[dict[str, Any]] = Field(default_factory=list)
