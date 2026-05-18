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
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.integrations.manager import IntegrationManager
from app.integrations.path_mapping import (
    PathMapping,
    parse_mappings,
    remap_path_chain,
)
from app.models.integration import Integration
from app.models.media import MediaFile
from app.models.path_mapping import GlobalPathMapping
from app.models.playback import PlaybackSession
from app.schemas.playback import (
    CursorRead,
    DecisionDayPoint,
    DecisionTrendResponse,
    DeviceMatrixCell,
    DeviceMatrixResponse,
    LivePlaybackResponse,
    LivePlaybackSession,
    PlaybackEventRead,
    PlaybackEventsPage,
    TopTranscodedFile,
    TopTranscodedResponse,
)
from app.security.secrets import get_secret_box
from app.services.playback.stats import (
    PlaybackFilter,
    PlaybackStatsService,
)
from app.utils.datetime import utcnow

router = APIRouter(prefix="/playback", tags=["playback"])

log = get_logger("auditarr.playback.api", category="playback")


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


# ── Stage 09 (v1.7) — live playback aggregate ──────────────────


@router.get(
    "/live",
    response_model=LivePlaybackResponse,
    summary="Stage 09: list currently-active playback sessions",
)
async def list_live_playbacks(
    _user: CurrentUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> LivePlaybackResponse:
    """Aggregate currently-active playback sessions across every
    enabled Plex/Jellyfin integration.

    Plan §484 — the dashboard's "Live now" tile reads this on a
    15-second poll. Providers that don't implement
    ``fetch_live_playbacks`` contribute nothing (Sonarr, Radarr,
    Bazarr, Tdarr) — they're skipped by the hasattr-check.

    Path mappings are applied per integration before returning
    so paths line up with library files. When the remapped
    path matches a known ``MediaFile``, the row's id is
    included so the frontend can deep-link.

    Provider errors degrade silently: one Plex temporarily
    unreachable shouldn't blank the tile for the operator's
    other integrations.
    """
    manager = IntegrationManager(
        session=session,
        registry=registry,
        secret_box=get_secret_box(),
        event_bus=bus,
    )

    enabled = (
        await session.execute(
            select(Integration).where(Integration.enabled.is_(True))
        )
    ).scalars().all()

    # Global mappings — one fetch shared across all integrations.
    global_rows = (
        await session.execute(
            select(GlobalPathMapping)
            .where(GlobalPathMapping.enabled.is_(True))
            .order_by(
                GlobalPathMapping.priority.asc(),
                GlobalPathMapping.created_at.asc(),
            )
        )
    ).scalars().all()
    global_mappings = [
        PathMapping(
            src_prefix=r.from_path.rstrip("/"),
            dst_prefix=r.to_path.rstrip("/"),
        )
        for r in global_rows
        if r.from_path and r.to_path
    ]

    out: list[LivePlaybackSession] = []
    # Resolve once at the end so we do one IN query rather than
    # N point lookups.
    pending_path_to_dto: list[tuple[str, LivePlaybackSession]] = []

    # Index integrations by id for the table-read path below so
    # we can pull integration_name / config without a per-row
    # query.
    integration_by_id = {ig.id: ig for ig in enabled}

    # ── v1.8.0 (Stage 17): Plex sessions come from the DB ──────
    # The worker's SSE listener writes to playback_sessions in
    # real time. Reading the table here is faster AND captures
    # short / aborted sessions that the old per-poll path missed.
    plex_integration_ids = [
        ig.id for ig in enabled if ig.kind == "plex"
    ]
    plex_rows: list[PlaybackSession] = []
    if plex_integration_ids:
        plex_rows_iter = await session.execute(
            select(PlaybackSession).where(
                PlaybackSession.integration_id.in_(plex_integration_ids),
                PlaybackSession.state != "stopped",
            )
        )
        plex_rows = list(plex_rows_iter.scalars().all())

    for row in plex_rows:
        ig = integration_by_id.get(row.integration_id)
        if ig is None:
            continue
        ig_mappings = parse_mappings((ig.config or {}).get("path_mappings"))
        source_path = row.source_path or ""
        mapped_path = (
            remap_path_chain(source_path, ig_mappings, global_mappings)
            if source_path
            else ""
        )
        # progress_pct derived from view_offset_ms / duration_ms.
        if row.view_offset_ms is not None and row.duration_ms:
            progress_pct: float | None = max(
                0.0,
                min(100.0, round(row.view_offset_ms / row.duration_ms * 100, 1)),
            )
        else:
            progress_pct = None
        live_row = LivePlaybackSession(
            integration_id=row.integration_id,
            integration_name=ig.name,
            integration_kind="plex",
            upstream_id=row.session_key,
            source_path=mapped_path,
            decision=row.decision,
            state=row.state,
            started_at=row.started_at,
            progress_pct=progress_pct,
            user=row.user,
            device_kind=row.device_kind,
            device_name=row.device_name,
            source_codec=row.source_codec,
            source_bitrate_kbps=row.source_bitrate_kbps,
            source_width=row.source_width,
            source_height=row.source_height,
            source_container=row.source_container,
            target_codec=row.target_codec,
            target_bitrate_kbps=row.target_bitrate_kbps,
            title=row.title,
            media_file_id=row.media_file_id,
        )
        out.append(live_row)
        if mapped_path and live_row.media_file_id is None:
            pending_path_to_dto.append((mapped_path, live_row))

    # ── Non-Plex integrations (Jellyfin etc) still poll inline ──
    # Jellyfin doesn't expose SSE; until we wire its equivalent
    # (WebSocket session notifications, planned for v1.8.x) we
    # keep the per-poll path for these providers.
    for integration in enabled:
        if integration.kind == "plex":
            continue
        provider = manager.provider_for(integration.kind)
        if provider is None or not hasattr(provider, "fetch_live_playbacks"):
            continue
        try:
            config = manager.build_config(integration)
            live_dtos = await provider.fetch_live_playbacks(config)
        except Exception as exc:  # noqa: BLE001
            # One bad provider must not blank the tile; the
            # integration's healthcheck cron surfaces the
            # outage elsewhere. We DO log the failure though —
            # silently swallowing meant "doesn't work" reports
            # had no signal in the logs to debug from.
            log.warning(
                "playback.live.provider_failed",
                integration_id=integration.id,
                integration_kind=integration.kind,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            continue

        ig_mappings = parse_mappings(
            (integration.config or {}).get("path_mappings")
        )
        for dto in live_dtos:
            mapped_path = remap_path_chain(
                dto.source_path, ig_mappings, global_mappings
            )
            row = LivePlaybackSession(
                integration_id=integration.id,
                integration_name=integration.name,
                integration_kind=integration.kind,
                upstream_id=dto.upstream_id,
                source_path=mapped_path,
                decision=dto.decision,
                state=dto.state,
                started_at=dto.started_at,
                progress_pct=dto.progress_pct,
                user=dto.user,
                device_kind=dto.device_kind,
                device_name=dto.device_name,
                source_codec=dto.source_codec,
                source_bitrate_kbps=dto.source_bitrate_kbps,
                source_width=dto.source_width,
                source_height=dto.source_height,
                source_container=dto.source_container,
                target_codec=dto.target_codec,
                target_bitrate_kbps=dto.target_bitrate_kbps,
                title=dto.title,
                media_file_id=None,  # filled after the batched lookup.
            )
            out.append(row)
            pending_path_to_dto.append((mapped_path, row))

    # Batched MediaFile lookup so the frontend gets a deep-link
    # id where possible.
    if pending_path_to_dto:
        paths = list({p for p, _ in pending_path_to_dto})
        rows = await session.execute(
            select(MediaFile.path, MediaFile.id).where(
                MediaFile.path.in_(paths)
            )
        )
        path_to_id = {r[0]: r[1] for r in rows.all()}
        for path, dto_row in pending_path_to_dto:
            dto_row.media_file_id = path_to_id.get(path)

    resolved = sum(1 for s in out if s.media_file_id is not None)
    return LivePlaybackResponse(
        sessions=out,
        total=len(out),
        resolved=resolved,
        unresolved=len(out) - resolved,
        polled_at=utcnow(),
    )


# ── v1.9 Stage 9.1 — Device index ──────────────────────────────


@router.get(
    "/devices",
    summary="List observed playback devices ranked by play count",
)
async def list_devices(
    _user: CurrentUser,
    session: SessionDep,
    limit: int = 50,
) -> dict[str, object]:
    """Return the device index. Default 50 rows; sorted by total
    ``playback_count`` desc so the dashboard's "Devices observed"
    card surfaces the most active devices first.

    Returns ``{devices: [...], total: N}`` so the dashboard
    knows when more rows exist than the limit shows.

    Each device row carries the decision-split counters so the
    dashboard can render a transcode-ratio bar without
    additional queries:

        transcode_count / playback_count = ratio
    """
    from sqlalchemy import func, select

    from app.models.playback_device import PlaybackDevice

    capped = max(1, min(int(limit), 500))
    total = (
        await session.execute(
            select(func.count()).select_from(PlaybackDevice)
        )
    ).scalar_one()

    rows = (
        (
            await session.execute(
                select(PlaybackDevice)
                .order_by(PlaybackDevice.playback_count.desc())
                .limit(capped)
            )
        )
        .scalars()
        .all()
    )

    devices = [
        {
            "id": d.id,
            "integration_id": d.integration_id,
            "client_key": d.client_key,
            "name": d.name,
            "platform": d.platform,
            "product": d.product,
            "device_model": d.device_model,
            "first_seen_at": (
                d.first_seen_at.isoformat() if d.first_seen_at else None
            ),
            "last_seen_at": (
                d.last_seen_at.isoformat() if d.last_seen_at else None
            ),
            "playback_count": d.playback_count,
            "transcode_count": d.transcode_count,
            "direct_play_count": d.direct_play_count,
            "direct_stream_count": d.direct_stream_count,
        }
        for d in rows
    ]
    return {"devices": devices, "total": total}
