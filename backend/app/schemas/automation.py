"""Automation API schemas."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Job catalogue ───────────────────────────────────────────────
class JobKindRead(BaseModel):
    key: str
    label: str
    description: str
    args_schema: dict[str, Any]
    required_args: list[str]
    timeout_seconds: int


# ── Schedules ───────────────────────────────────────────────────
class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    job_kind: str = Field(min_length=1, max_length=64)
    job_args: dict[str, Any] = Field(default_factory=dict)
    cron: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=3600, ge=1, le=24 * 60 * 60)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    job_args: dict[str, Any] | None = None
    cron: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=24 * 60 * 60)


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    enabled: bool
    job_kind: str
    job_args: dict[str, Any]
    cron: dict[str, Any]
    next_run_at: _dt.datetime | None
    last_run_at: _dt.datetime | None
    last_status: str | None
    timeout_seconds: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ── Job runs ────────────────────────────────────────────────────
class JobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schedule_id: str | None
    job_kind: str
    job_args: dict[str, Any]
    status: str
    started_at: _dt.datetime
    finished_at: _dt.datetime | None
    duration_ms: int | None
    result: dict[str, Any] | None
    error: str | None
    trigger: str


# ── Manual run request ──────────────────────────────────────────
class JobRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_kind: str = Field(min_length=1, max_length=64)
    job_args: dict[str, Any] = Field(default_factory=dict)


# ── Optimization queue ──────────────────────────────────────────
class OptimizationItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_file_id: str
    profile: str
    status: str
    queued_by_rule_id: str | None
    queued_at: _dt.datetime
    item_metadata: dict[str, Any]
    error: str | None
    created_at: _dt.datetime
    updated_at: _dt.datetime
