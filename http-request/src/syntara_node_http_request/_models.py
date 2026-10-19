"""Pydantic models for the HTTP request step."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class HttpInput(BaseModel):
    url: str
    method: HttpMethod = HttpMethod.GET
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | str | None = None
    query_params: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=3600)
    follow_redirects: bool = False


class HttpOutput(BaseModel):
    status_code: int
    body: Any
    headers: dict[str, str]
    elapsed: float
