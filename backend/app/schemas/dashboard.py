"""Dashboard API schemas."""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, ConfigDict


class SeverityCountsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ok: int
    info: int
    warn: int
    high: int
    error: int
    crit: int
    total: int


class OptimizationCountsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    queued: int
    running: int
    completed: int
    failed: int


class DashboardOverviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_count: int
    library_count: int
    integration_count: int
    integration_ok_count: int
    rule_count: int
    rule_enabled_count: int
    severity_counts: SeverityCountsRead
    issues_open: int
    optimization_counts: OptimizationCountsRead
    last_scan_at: _dt.datetime | None
    total_size_bytes: int = 0


class LibrarySeverityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    library_id: str
    library_name: str
    file_count: int
    severity: SeverityCountsRead


class IntegrationHealthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    integration_id: str
    name: str
    kind: str
    enabled: bool
    health_status: str
    health_detail: str | None
    health_checked_at: _dt.datetime | None


class TopRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    name: str
    enabled: bool
    match_count: int


class RecentScanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    library_id: str
    library_name: str
    mode: str
    status: str
    files_seen: int
    started_at: _dt.datetime | None
    finished_at: _dt.datetime | None


class RecentJobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_kind: str
    status: str
    trigger: str
    started_at: _dt.datetime
    duration_ms: int | None
    error: str | None


class SidebarBadgesRead(BaseModel):
    issuesOpen: int
    rulesEnabled: int
    activeOptimizations: int


# Stage 14.1: dashboard sparkline series.
class DashboardSeriesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    days: int
    issues_opened: list[int]
    issues_resolved: list[int]
    integrity_score: list[float]
    files_seen: list[int]


# Stage 26: library composition by codec / container.
class CategoryBreakdownRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    group: str  # "video_codec" | "container"
    file_count: int
    total_size_bytes: int
