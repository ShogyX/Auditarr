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


# ── v1.9 Stage 3.3 — Composition ──────────────────────────────


class CompositionRowRead(BaseModel):
    """One row in any of the composition sections."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    count: int
    total_size_bytes: int = 0


class BitrateMatrixRowRead(BaseModel):
    """One row in the median-bitrate matrix."""

    model_config = ConfigDict(from_attributes=True)

    library_id: str | None
    library_name: str | None
    resolution_key: str
    video_codec: str | None
    container: str | None
    file_count: int
    median_bitrate_kbps: int


class CompositionRead(BaseModel):
    """Full composition payload for the new Categories card."""

    model_config = ConfigDict(from_attributes=True)

    resolutions: list[CompositionRowRead]
    extensions: list[CompositionRowRead]
    containers: list[CompositionRowRead]
    subtitle_formats: list[CompositionRowRead]
    subtitle_languages: list[CompositionRowRead]
    audio_languages: list[CompositionRowRead]
    unknown_tracks: dict[str, int]
    subtitles_internal_external: dict[str, int]
    orphan_count: int
    bitrate_matrix: list[BitrateMatrixRowRead]


# v1.9 Stage 9.5.7 (OP-8 / OP-9) — language-preference + rule-flagged surfaces.


class ForeignAudioSummaryRead(BaseModel):
    """Foreign-audio surfacing for the dashboard.

    A file qualifies when:
      * its primary audio track's language is NOT in
        ``preferred_audio_languages``, AND
      * it carries NO subtitle track in any of
        ``preferred_subtitle_languages``.

    The operator-configurable preferences are echoed back in
    the response so the UI's "settings → operator preferences"
    callout can render the active values without a second
    fetch.
    """

    model_config = ConfigDict(from_attributes=True)

    count: int
    sample_ids: list[str]
    preferred_audio_languages: list[str]
    preferred_subtitle_languages: list[str]


class IncompatibleMediaSummaryRead(BaseModel):
    """Rule-flagged incompatible-media count for the dashboard.

    Any file with at least one ``rule_evaluations`` row tagged
    by a rule whose action set carries ``incompatible_audio``
    or ``incompatible_video`` semantics (via the rule's
    actions_summary). The matching rules are operator-authored;
    this surface just counts files where any such rule fired.
    """

    model_config = ConfigDict(from_attributes=True)

    count: int
    sample_ids: list[str]
