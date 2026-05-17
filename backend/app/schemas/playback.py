"""Playback insights — Pydantic response models.

Stage 12 (audit follow-up). The poller (``app/services/playback/poller.py``)
already writes ``PlaybackEvent`` and ``IntegrationPollingCursor`` rows
and the analyzer reads them, but no read API existed for operators.
These schemas back the new read endpoints under ``/api/v1/playback``.

The PlaybackEvent ORM model has more columns than we surface (e.g.
``upstream_id``, an internal idempotency key the poller uses to
dedupe). That field is intentionally omitted here — operators have
no use for it and exposing it would invite confusion.
"""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, ConfigDict


class PlaybackEventRead(BaseModel):
    """One playback observation as the UI sees it.

    The shape mirrors :class:`app.models.playback.PlaybackEvent`
    minus ``upstream_id`` (internal) plus two joined fields
    (``library_name``, ``integration_name``) so the UI doesn't have
    to do a second round-trip just to render the row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    integration_id: str
    integration_name: str | None = None
    media_file_id: str | None = None
    library_id: str | None = None
    library_name: str | None = None
    source_path: str
    device_kind: str | None = None
    device_name: str | None = None
    decision: str
    reason_code: str | None = None
    source_codec: str | None = None
    source_bitrate_kbps: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_container: str | None = None
    target_codec: str | None = None
    target_bitrate_kbps: int | None = None
    started_at: _dt.datetime
    completed_at: _dt.datetime | None = None
    duration_s: int | None = None


class PlaybackEventsPage(BaseModel):
    """Cursorless paginated response — mirrors ``MediaPage``."""

    model_config = ConfigDict(from_attributes=True)

    items: list[PlaybackEventRead]
    total: int
    offset: int
    limit: int


class TopTranscodedFile(BaseModel):
    """One row in the top-transcoded-files panel.

    Rows where ``media_file_id`` is null in the underlying table are
    aggregated into a single sentinel bucket with ``media_file_id =
    None`` and ``path = "<unresolved>"``. The frontend can render
    that bucket separately.
    """

    model_config = ConfigDict(from_attributes=True)

    media_file_id: str | None = None
    path: str
    filename: str | None = None
    transcode_count: int
    last_transcoded_at: _dt.datetime | None = None
    source_codec: str | None = None
    target_codec: str | None = None


class TopTranscodedResponse(BaseModel):
    items: list[TopTranscodedFile]
    window_days: int


class DeviceMatrixCell(BaseModel):
    """One ``(device_kind, decision)`` cell in the matrix."""

    model_config = ConfigDict(from_attributes=True)

    device_kind: str
    decision: str
    count: int


class DeviceMatrixResponse(BaseModel):
    cells: list[DeviceMatrixCell]
    window_days: int


class DecisionDayPoint(BaseModel):
    """One day's playcount per decision for the stacked sparkline."""

    model_config = ConfigDict(from_attributes=True)

    day: _dt.date
    decision: str
    count: int


class DecisionTrendResponse(BaseModel):
    points: list[DecisionDayPoint]
    window_days: int


class CursorRead(BaseModel):
    """One ``IntegrationPollingCursor`` row.

    Stage 12 exposes this for debugging — operators can see how
    stale each integration's poll watermark is. The reset endpoint
    deletes these rows so the next poll tick re-walks from the
    integration's defined "start" position.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    integration_id: str
    integration_name: str | None = None
    integration_kind: str | None = None
    cursor_kind: str
    cursor_value: str
    updated_at: _dt.datetime


# ── Stage 09 (v1.7) — live playback ─────────────────────────────


class LivePlaybackSession(BaseModel):
    """One in-progress playback session as returned by
    :func:`app.api.v1.playback.list_live_playbacks`.

    Differs from :class:`PlaybackEventRead` in that nothing
    here is persisted — the dashboard's "Live now" tile is a
    realtime view sourced directly from the integration's
    /sessions endpoint via the provider's
    ``fetch_live_playbacks`` method.

    ``source_path`` is post-remap: the aggregating endpoint
    applies the integration's path mappings before returning
    so the frontend sees Auditarr-side paths and can link to
    library files when resolved.
    """

    integration_id: str
    integration_name: str
    integration_kind: str
    upstream_id: str
    source_path: str
    decision: str
    state: str
    started_at: _dt.datetime
    progress_pct: float | None = None
    user: str | None = None
    device_kind: str | None = None
    device_name: str | None = None
    source_codec: str | None = None
    source_bitrate_kbps: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_container: str | None = None
    target_codec: str | None = None
    target_bitrate_kbps: int | None = None
    title: str | None = None
    #: Stage 09 (addendum A.7) — when the post-remap path
    #: matches a known MediaFile, the matched row's id. Lets the
    #: frontend deep-link from the tile to the file's detail
    #: drawer. ``None`` when path mappings haven't caught the
    #: file — the frontend can hint at "Configure path mappings".
    media_file_id: str | None = None


class LivePlaybackResponse(BaseModel):
    """Aggregated response for ``GET /playback/live``.

    Returns a flat list of sessions across every enabled
    Plex/Jellyfin integration plus a small summary counter so
    the frontend can render "N playing now" without re-counting.
    """

    sessions: list[LivePlaybackSession]
    total: int
    resolved: int
    unresolved: int
    polled_at: _dt.datetime
