"""Optimization API schemas."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Profiles ────────────────────────────────────────────────────
class OptimizationProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)
    max_input_bytes: int | None = Field(default=None, ge=1)
    # Stage 7 (audit follow-up): optional integration routing.
    # NULL ⇒ in-process ffmpeg runner. See model docstring for full
    # rationale.
    optimization_integration_id: str | None = Field(
        default=None, max_length=36
    )


class OptimizationProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None
    max_input_bytes: int | None = Field(default=None, ge=1)
    optimization_integration_id: str | None = Field(
        default=None, max_length=36
    )


class OptimizationProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    enabled: bool
    settings: dict[str, Any]
    max_input_bytes: int | None
    optimization_integration_id: str | None = None
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ── Queue items ─────────────────────────────────────────────────
class OptimizationItemDetailRead(BaseModel):
    """Stage 10 expands on the Stage 7 read schema with worker fields."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    media_file_id: str
    profile: str
    status: str
    queued_by_rule_id: str | None
    queued_at: _dt.datetime
    started_at: _dt.datetime | None
    finished_at: _dt.datetime | None
    progress_pct: int
    original_size_bytes: int | None
    optimized_size_bytes: int | None
    backup_path: str | None
    item_metadata: dict[str, Any]
    error: str | None
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ── Manual enqueue ──────────────────────────────────────────────
class OptimizationEnqueueRequest(BaseModel):
    """Queue a specific (file, profile) pair manually from the UI."""

    model_config = ConfigDict(extra="forbid")

    media_file_id: str
    profile: str = Field(min_length=1, max_length=64)


# ── Stage 28: bulk enqueue ──────────────────────────────────────
class OptimizationBulkEnqueueRequest(BaseModel):
    """Queue many files against one profile in a single call.

    The 500-item ceiling matches every other bulk endpoint in the
    project; selection size is bounded by the Files page max-page,
    and the bulk endpoint should never invite library-scale rework
    that the per-library rules-evaluation flow already serves.
    """

    model_config = ConfigDict(extra="forbid")

    media_ids: list[str] = Field(min_length=1, max_length=500)
    profile: str = Field(min_length=1, max_length=64)


class OptimizationBulkEnqueueResponse(BaseModel):
    """Per-bucket outcome breakdown.

    - ``queued``: new (file, profile) pairs that landed in the queue
      for the first time (or were re-queued after a prior failure).
    - ``already_queued``: pairs that were already in ``queued``
      state; the upsert refreshed ``queued_at`` but didn't add a
      duplicate row.
    - ``skipped_active``: pairs whose existing entry was in
      ``running``/``completed``/``failed``/``cancelled``/``skipped``
      state and was left alone. The operator can use Retry on the
      Optimization page to re-queue these explicitly.
    - ``files_not_found``: ids that didn't resolve to a media row.
    """

    queued: int
    already_queued: int
    skipped_active: int
    files_not_found: list[str]


# ── Worker tick result ──────────────────────────────────────────
class WorkerReportRead(BaseModel):
    item_id: str | None
    status: str
    detail: str | None
