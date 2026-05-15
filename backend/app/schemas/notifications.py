"""Notifications API schemas."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Available kinds ─────────────────────────────────────────
class NotificationKind(BaseModel):
    kind: str
    label: str
    config_schema: dict[str, Any]
    secret_fields: list[str]


# ── Channel CRUD ────────────────────────────────────────────
class NotificationChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any] = Field(default_factory=dict)
    min_severity_rank: int = Field(default=40, ge=0, le=100)


class NotificationChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None
    min_severity_rank: int | None = Field(default=None, ge=0, le=100)


class NotificationChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    enabled: bool
    config: dict[str, Any]
    min_severity_rank: int
    last_delivery_status: str | None
    last_delivery_at: _dt.datetime | None
    last_delivery_error: str | None
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ── Test send ───────────────────────────────────────────────
class NotificationTestRequest(BaseModel):
    """Trigger a one-off test send against an existing channel."""

    model_config = ConfigDict(extra="forbid")

    severity: str = Field(default="info")
    message: str | None = Field(default=None, max_length=2000)


# ── Deliveries ──────────────────────────────────────────────
class NotificationDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str | None
    channel_name: str
    channel_kind: str
    status: str
    severity: str
    subject: str
    body: str
    context: dict[str, Any]
    attempted_at: _dt.datetime
    completed_at: _dt.datetime | None
    duration_ms: int | None
    error: str | None
