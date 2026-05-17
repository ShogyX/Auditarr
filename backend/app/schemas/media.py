"""Pydantic schemas for the media API surface."""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Audit follow-up: a library row was found in production with the
# value ``" /media/NAS-Pool/media/AnimeMovies"`` (note the leading
# space) which broke the scanner with a ``FileNotFoundError``. The
# UI's text input doesn't auto-trim, and the schema didn't either,
# so a stray paste-time space made it through to disk. Strip
# whitespace at the schema layer so this can't happen again from
# any client.
def _strip_str(v: object) -> object:
    if isinstance(v, str):
        return v.strip()
    return v


# ── Library ──────────────────────────────────────────────────
class LibraryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1, max_length=1024)
    kind: str = Field(default="movies", pattern=r"^(movies|tv|music|mixed)$")
    enabled: bool = True
    scan_interval_minutes: int = Field(default=0, ge=0, le=24 * 60 * 7)
    integration_link: dict | None = None

    _strip_text = field_validator("name", "root_path", mode="before")(_strip_str)


class LibraryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    root_path: str | None = Field(default=None, min_length=1, max_length=1024)
    kind: str | None = Field(default=None, pattern=r"^(movies|tv|music|mixed)$")
    enabled: bool | None = None
    scan_interval_minutes: int | None = Field(default=None, ge=0, le=24 * 60 * 7)
    integration_link: dict | None = None

    _strip_text = field_validator("name", "root_path", mode="before")(_strip_str)


class LibraryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    root_path: str
    kind: str
    enabled: bool
    scan_interval_minutes: int
    integration_link: dict | None
    last_scan_at: _dt.datetime | None
    last_scan_status: str | None
    last_scan_file_count: int | None
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ── Media file ───────────────────────────────────────────────
class MatchedRuleSummary(BaseModel):
    """A single rule-match entry attached to a file summary.

    Stage 3 (audit follow-up): the Files table needs to render the
    list of matched rules per row without paying for a per-row fetch.
    The summary is intentionally tiny — just enough for a chip strip.
    Full evaluation details (action summary, evaluated_at, etc.) still
    require ``GET /media/{id}/evaluations``.
    """

    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    rule_name: str
    severity: str


class MediaFileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    library_id: str
    path: str
    relative_path: str
    filename: str
    extension: str
    size_bytes: int
    mtime: _dt.datetime
    category: str
    severity: str
    severity_rank: int
    container: str | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    has_subtitles: bool
    is_orphaned: bool
    # Stage 27 added a ``quarantined`` flag here. Stage 05 (v1.7)
    # removed it along with the rest of the quarantine workflow
    # (Section A.0 — "delete means delete"). A file is either in
    # the library or it's in ``data_dir/trash/`` after a rule
    # deleted it; there is no intermediate state on the row.
    # Stage 3 (audit follow-up): matched-rules chips for the Files
    # table. Default empty list so callers that don't request the
    # join (the dashboard summary endpoints, internal services) get
    # an absent value semantically equivalent to "we didn't ask".
    # The Files API turns on ``include_matched_rules`` and populates
    # this; the rest of the codebase doesn't change.
    matched_rules: list[MatchedRuleSummary] = []
    # Stage 13 (audit follow-up): tag names. Default empty list for
    # the same "we didn't ask" semantics as ``matched_rules``. The
    # Files API turns on ``include_tags`` when the optional tags
    # column is enabled; everywhere else the field is empty.
    tags: list[str] = []


class MediaTagRead(BaseModel):
    """One ``MediaTag`` row as the UI sees it.

    Stage 13 (audit follow-up): backs the dedicated
    ``GET /media/{id}/tags`` endpoint. Includes the source so the
    drawer can render manual / rule / integration tags as distinct
    chip groups.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source: str
    created_at: _dt.datetime


class MediaFileDetail(MediaFileSummary):
    duration_seconds: float | None
    bitrate_kbps: int | None
    subtitle_codec: str | None
    framerate: float | None
    subtitle_languages: list[str] | None
    audio_languages: list[str] | None
    probe: dict | None
    probe_failed: bool
    probe_error: str | None
    last_scan_id: str | None
    seen_at: _dt.datetime
    # Stage 27 added ``quarantined_at`` and ``quarantined_reason``
    # here. Stage 05 (v1.7) removed them — see ``MediaFileSummary``
    # comment above.
    # Stage 19 (audit follow-up): content hash + VirusTotal result.
    # All four nullable; the file drawer hides its Security section
    # when both ``hash_sha256`` and ``virustotal_result`` are None.
    hash_sha256: str | None = None
    hash_computed_at: _dt.datetime | None = None
    virustotal_result: dict | None = None
    virustotal_checked_at: _dt.datetime | None = None
    created_at: _dt.datetime
    updated_at: _dt.datetime


class MediaPageRead(BaseModel):
    items: list[MediaFileSummary]
    total: int
    offset: int
    limit: int


# ── Scan run ─────────────────────────────────────────────────
class ScanRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    library_id: str
    mode: str
    status: str
    started_at: _dt.datetime | None
    finished_at: _dt.datetime | None
    files_seen: int
    files_added: int
    files_updated: int
    files_orphaned: int
    probe_failures: int
    error: str | None
    created_at: _dt.datetime


class ScanTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="full", pattern=r"^(full|incremental|targeted|rescan)$")
    follow_symlinks: bool = False
