"""Updater API schemas."""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, ConfigDict, Field


class UpdaterStatusRead(BaseModel):
    installed_version: str
    latest_version: str | None
    has_update: bool
    last_checked_at: str | None
    last_check_ok: bool | None
    last_check_detail: str | None
    feed_url: str
    apply_in_progress: bool
    # Stage 19: install environment context for the UI.
    install_mode: str
    apply_enabled: bool
    # Stage 1.6 (v1.9.1) — populated for Docker installs with the
    # canonical host commands the operator must run to upgrade.
    # None for bare-metal (Apply button does the work) and unmanaged
    # (operator's own config tool drives the upgrade).
    manual_apply_command: str | None = None


class UpdateCheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    checked_at: _dt.datetime
    ok: bool
    latest_version: str | None
    changelog: str | None
    detail: str | None
    feed_url: str


class UpdateApplyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    from_version: str | None
    to_version: str
    started_at: _dt.datetime
    finished_at: _dt.datetime | None
    triggered_by_user_id: str | None
    detail: str | None
    error: str | None


class UpdateApplyRequest(BaseModel):
    """Body for ``POST /api/v1/updater/apply``."""

    model_config = ConfigDict(extra="forbid")

    to_version: str = Field(min_length=1, max_length=64)
