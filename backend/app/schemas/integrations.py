"""Integration API schemas."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntegrationKind(BaseModel):
    """An integration kind advertised by a loaded provider plugin."""

    kind: str
    label: str
    config_schema: dict[str, Any]
    secret_fields: list[str]


class IntegrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    poll_interval_seconds: int = Field(default=300, ge=0, le=24 * 60 * 60)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any] = Field(default_factory=dict)


class IntegrationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    poll_interval_seconds: int | None = Field(
        default=None, ge=0, le=24 * 60 * 60
    )
    config: dict[str, Any] | None = None
    # ``secrets`` is only included if the operator wants to rotate. Empty
    # dict (or omitted) leaves existing secrets untouched.
    secrets: dict[str, Any] | None = None


class IntegrationRead(BaseModel):
    """Public representation — never includes secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    enabled: bool
    poll_interval_seconds: int
    config: dict[str, Any]
    health_status: str
    health_detail: str | None
    health_checked_at: _dt.datetime | None
    created_at: _dt.datetime
    updated_at: _dt.datetime
    has_secrets: bool


class IntegrationHealthRead(BaseModel):
    integration_id: str
    status: str
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveredLibraryRead(BaseModel):
    upstream_id: str
    name: str
    kind: str
    root_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
