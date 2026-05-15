"""Shared response schemas used by API error/health handlers."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Uniform API error envelope."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class HealthStatus(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None
    duration_ms: float | None = None


class HealthResponse(BaseModel):
    status: str = Field(description="`ok` if all checks healthy, `degraded` otherwise")
    version: str
    checks: list[HealthStatus]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 50
