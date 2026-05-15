"""Playback insights read API (Stage 12 audit follow-up).

The poller writes ``PlaybackEvent`` rows; the analyzer reads them
to suggest rules. Pre-Stage-12 there was no way for operators to
SEE the data — the dashboard's transcode panels were intentionally
shelved because this read API didn't exist. This router fills the
gap.

Read endpoints are non-admin; the cursor-reset endpoint is admin-only.
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import SessionDep
from app.core.exceptions import NotFoundError
from app.models.integration import Integration
from app.schemas.playback import (
    CursorRead,
    DecisionDayPoint,
    DecisionTrendResponse,
    DeviceMatrixCell,
    DeviceMatrixResponse,
    PlaybackEventRead,
    PlaybackEventsPage,
    TopTranscodedFile,
    TopTranscodedResponse,
)
from app.services.playback.stats import (
    PlaybackFilter,
    PlaybackStatsService,
)

router = APIRouter(prefix="/playback", tags=["playback"])


# ── Constants ──────────────────────────────────────────────────
MAX_LIMIT = 500
DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365


# ── Events listing ─────────────────────────────────────────────
@router.get(
    "/events",
    response_model=PlaybackEventsPage,
    summary="Paginated playback events with optional filters (Stage 12)",
)
async def list_events(
    _user: CurrentUser,
    session: SessionDep,
    library_id: str | None = Query(default=None),
    integration_id: str | None = Query(default=None),
    media_file_id: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    device_kind: str | None = Query(default=None),
    since: _dt.datetime | None = Query(default=None),
    until: _dt.datetime | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> PlaybackEventsPage:
    """Return playback events ordered by ``started_at DESC``.

    Every filter parameter is optional. ``library_id`` joins through
    ``media_file → library`` so events whose path didn't resolve to
    an indexed media file are excluded when this filter is set.
    """
    filt = PlaybackFilter(
        library_id=library_id,
        integration_id=integration_id,
        media_file_id=media_file_id,
        decision=decision,
        device_kind=device_kind,
        since=since,
        until=until,
        offset=offset,
        limit=limit,
    )
    page = await PlaybackStatsService(session).list_events(filt)
    items = []
    for row in page.items:
        # Hydrate the joined names onto the read model.
        read = PlaybackEventRead.model_validate(row.event)
        read.integration_name = row.integration_name
        read.library_id = row.library_id
        read.library_name = row.library_name
        items.append(read)
    return PlaybackEventsPage(
        items=items,
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


# ── Top transcoded files ───────────────────────────────────────
@router.get(
    "/stats/transcoded",
    response_model=TopTranscodedResponse,
    summary="Top files by transcode count over the window (Stage 12)",
)
async def top_transcoded(
    _user: CurrentUser,
    session: SessionDep,
    days: int = Query(
        default=DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS
    ),
    limit: int = Query(default=20, ge=1, le=100),
) -> TopTranscodedResponse:
    rows = await PlaybackStatsService(session).top_transcoded(
        days=days, limit=limit
    )
    items = [
        TopTranscodedFile.model_validate(r, from_attributes=True)
        for r in rows
    ]
    return TopTranscodedResponse(items=items, window_days=days)


# ── Device matrix ──────────────────────────────────────────────
@router.get(
    "/stats/devices",
    response_model=DeviceMatrixResponse,
    summary="Counts grouped by (device_kind, decision) (Stage 12)",
)
async def device_matrix(
    _user: CurrentUser,
    session: SessionDep,
    days: int = Query(
        default=DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS
    ),
) -> DeviceMatrixResponse:
    rows = await PlaybackStatsService(session).device_matrix(days=days)
    cells = [
        DeviceMatrixCell.model_validate(r, from_attributes=True) for r in rows
    ]
    return DeviceMatrixResponse(cells=cells, window_days=days)


# ── Decision trend ─────────────────────────────────────────────
@router.get(
    "/stats/decisions",
    response_model=DecisionTrendResponse,
    summary="Daily rollup per decision for a stacked sparkline (Stage 12)",
)
async def decision_trend(
    _user: CurrentUser,
    session: SessionDep,
    days: int = Query(
        default=DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS
    ),
) -> DecisionTrendResponse:
    rows = await PlaybackStatsService(session).decision_trend(days=days)
    points = [
        DecisionDayPoint.model_validate(r, from_attributes=True) for r in rows
    ]
    return DecisionTrendResponse(points=points, window_days=days)


# ── Cursors ────────────────────────────────────────────────────
@router.get(
    "/cursors",
    response_model=list[CursorRead],
    summary="List every IntegrationPollingCursor (debug, Stage 12)",
)
async def list_cursors(
    _user: CurrentUser, session: SessionDep
) -> list[CursorRead]:
    rows = await PlaybackStatsService(session).list_cursors()
    out: list[CursorRead] = []
    for row in rows:
        read = CursorRead.model_validate(row.cursor)
        read.integration_name = row.integration_name
        read.integration_kind = row.integration_kind
        out.append(read)
    return out


@router.post(
    "/cursors/{integration_id}/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset every cursor for an integration (admin, Stage 12)",
)
async def reset_cursors(
    integration_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> None:
    """Clear every cursor row for ``integration_id`` so the next
    poll tick re-walks from the integration's starting position.

    Returns 404 if the integration doesn't exist (helps catch typos
    in the path); otherwise commits the deletion and returns 204.
    Even if zero cursors existed, the call succeeds — operators
    sometimes preemptively reset before the first poll.
    """
    integ = (
        await session.execute(
            select(Integration).where(Integration.id == integration_id)
        )
    ).scalar_one_or_none()
    if integ is None:
        raise NotFoundError(f"Integration {integration_id!r} not found")
    await PlaybackStatsService(session).reset_cursors_for_integration(
        integration_id
    )
    await session.commit()
