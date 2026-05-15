"""Pure-SQL aggregations over ``playback_events``.

Stage 12 (audit follow-up). Mirrors the pattern in
``app/services/dashboard/stats.py`` — read-only methods that
return structured dataclasses the API layer maps to Pydantic
schemas. Keeping the SQL out of the API surface means the queries
can grow without churning the public contract.

The poller and analyzer are explicitly out of scope; this module
only READS ``playback_events`` and ``integration_polling_cursors``.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from sqlalchemy import (
    desc,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import IntegrationPollingCursor, PlaybackEvent


# ── Filter dataclass ──────────────────────────────────────────
@dataclass(slots=True)
class PlaybackFilter:
    """Mirrors ``MediaFilter`` shape — collected into one object so
    the API surface and the service-layer stay decoupled."""

    library_id: str | None = None
    integration_id: str | None = None
    media_file_id: str | None = None
    decision: str | None = None
    device_kind: str | None = None
    since: _dt.datetime | None = None
    until: _dt.datetime | None = None
    offset: int = 0
    limit: int = 50


@dataclass(slots=True)
class PlaybackEventRow:
    """One event row + the two joined names."""

    event: PlaybackEvent
    integration_name: str | None
    library_id: str | None
    library_name: str | None


@dataclass(slots=True)
class PlaybackEventPage:
    items: list[PlaybackEventRow]
    total: int
    offset: int
    limit: int


@dataclass(slots=True)
class TopTranscodedRow:
    media_file_id: str | None
    path: str
    filename: str | None
    transcode_count: int
    last_transcoded_at: _dt.datetime | None
    source_codec: str | None
    target_codec: str | None


@dataclass(slots=True)
class DeviceMatrixRow:
    device_kind: str
    decision: str
    count: int


@dataclass(slots=True)
class DecisionDayRow:
    day: _dt.date
    decision: str
    count: int


@dataclass(slots=True)
class CursorRow:
    cursor: IntegrationPollingCursor
    integration_name: str | None
    integration_kind: str | None


# ── Service ────────────────────────────────────────────────────
class PlaybackStatsService:
    """Read-only aggregations + paginated event listing."""

    # Cap the page size at the API layer too — defence in depth.
    MAX_LIMIT = 500

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Event listing ────────────────────────────────────────
    async def list_events(
        self, filt: PlaybackFilter
    ) -> PlaybackEventPage:
        """Paginated event listing with optional filters.

        Joins ``integrations`` for the integration name. When the
        event has a resolved ``media_file_id``, joins ``media_files``
        + ``libraries`` for library context. Unresolved events
        (``media_file_id IS NULL``) still appear, just with null
        library fields.
        """
        # Base statement: SELECT event + names. The library join is
        # via media_file → library so unresolved events still match
        # (LEFT OUTER JOIN preserves NULL on the right side).
        base = (
            select(
                PlaybackEvent,
                Integration.name.label("integration_name"),
                MediaFile.library_id.label("library_id"),
                Library.name.label("library_name"),
            )
            .join(Integration, Integration.id == PlaybackEvent.integration_id)
            .outerjoin(
                MediaFile, MediaFile.id == PlaybackEvent.media_file_id
            )
            .outerjoin(Library, Library.id == MediaFile.library_id)
        )
        base = self._apply_filters(base, filt)
        base = base.order_by(desc(PlaybackEvent.started_at))

        # Count BEFORE pagination so the UI knows the true total.
        # Use a separate count statement to avoid the row-set
        # multiplication of joins. We approximate the same filter
        # surface here by repeating the joins where needed for
        # filter columns to remain bound.
        count_stmt = (
            select(func.count(PlaybackEvent.id))
            .join(
                Integration, Integration.id == PlaybackEvent.integration_id
            )
            .outerjoin(
                MediaFile, MediaFile.id == PlaybackEvent.media_file_id
            )
        )
        count_stmt = self._apply_filters(count_stmt, filt, for_count=True)
        total = (await self._session.execute(count_stmt)).scalar_one()

        # Bounded pagination.
        limit = min(max(1, filt.limit), self.MAX_LIMIT)
        offset = max(0, filt.offset)
        result = await self._session.execute(base.offset(offset).limit(limit))
        items: list[PlaybackEventRow] = []
        for row in result.all():
            event, integration_name, library_id, library_name = row
            items.append(
                PlaybackEventRow(
                    event=event,
                    integration_name=integration_name,
                    library_id=library_id,
                    library_name=library_name,
                )
            )
        return PlaybackEventPage(
            items=items, total=total, offset=offset, limit=limit
        )

    def _apply_filters(
        self,
        stmt: "select",
        filt: PlaybackFilter,
        *,
        for_count: bool = False,
    ) -> "select":
        if filt.integration_id:
            stmt = stmt.where(
                PlaybackEvent.integration_id == filt.integration_id
            )
        if filt.media_file_id:
            stmt = stmt.where(
                PlaybackEvent.media_file_id == filt.media_file_id
            )
        if filt.library_id:
            # ``library_id`` lives on MediaFile, joined-in already.
            stmt = stmt.where(MediaFile.library_id == filt.library_id)
        if filt.decision:
            stmt = stmt.where(PlaybackEvent.decision == filt.decision)
        if filt.device_kind:
            stmt = stmt.where(PlaybackEvent.device_kind == filt.device_kind)
        if filt.since:
            stmt = stmt.where(PlaybackEvent.started_at >= filt.since)
        if filt.until:
            stmt = stmt.where(PlaybackEvent.started_at <= filt.until)
        return stmt

    # ── Top transcoded files ─────────────────────────────────
    async def top_transcoded(
        self, *, days: int, limit: int
    ) -> list[TopTranscodedRow]:
        """Count transcodes per file over the trailing ``days`` window.

        Rows where ``media_file_id IS NULL`` (path didn't resolve to
        an indexed media file) are aggregated into a single
        ``<unresolved>`` bucket at the end. This is the "noisy
        unindexed Plex library" case the plan calls out.
        """
        # Cap limit defensively.
        limit = min(max(1, limit), 100)
        since = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)

        # Two queries: resolved bucket (group-by media_file_id) and
        # unresolved bucket (single row, media_file_id IS NULL).
        # Doing it as one query with a UNION would let the database
        # do the work, but two queries are easier to reason about
        # and the row counts are small.
        resolved_stmt = (
            select(
                PlaybackEvent.media_file_id,
                MediaFile.path,
                MediaFile.filename,
                func.count(PlaybackEvent.id).label("transcode_count"),
                func.max(PlaybackEvent.started_at).label("last_transcoded_at"),
                # source/target codec — take the most-common via
                # MAX as a proxy. For a true mode we'd need a
                # window function, but MAX is a reasonable cheap
                # approximation and the UI shows a single value
                # per file anyway.
                func.max(PlaybackEvent.source_codec).label("source_codec"),
                func.max(PlaybackEvent.target_codec).label("target_codec"),
            )
            .join(MediaFile, MediaFile.id == PlaybackEvent.media_file_id)
            .where(
                PlaybackEvent.decision == "transcode",
                PlaybackEvent.started_at >= since,
                PlaybackEvent.media_file_id.is_not(None),
            )
            .group_by(
                PlaybackEvent.media_file_id, MediaFile.path, MediaFile.filename
            )
            .order_by(desc("transcode_count"))
            .limit(limit)
        )
        resolved_rows = (await self._session.execute(resolved_stmt)).all()
        rows: list[TopTranscodedRow] = [
            TopTranscodedRow(
                media_file_id=r.media_file_id,
                path=r.path,
                filename=r.filename,
                transcode_count=int(r.transcode_count),
                last_transcoded_at=r.last_transcoded_at,
                source_codec=r.source_codec,
                target_codec=r.target_codec,
            )
            for r in resolved_rows
        ]

        # Unresolved bucket.
        unresolved_stmt = select(
            func.count(PlaybackEvent.id).label("transcode_count"),
            func.max(PlaybackEvent.started_at).label("last_transcoded_at"),
        ).where(
            PlaybackEvent.decision == "transcode",
            PlaybackEvent.started_at >= since,
            PlaybackEvent.media_file_id.is_(None),
        )
        u = (await self._session.execute(unresolved_stmt)).one()
        if u.transcode_count and int(u.transcode_count) > 0:
            rows.append(
                TopTranscodedRow(
                    media_file_id=None,
                    path="<unresolved>",
                    filename=None,
                    transcode_count=int(u.transcode_count),
                    last_transcoded_at=u.last_transcoded_at,
                    source_codec=None,
                    target_codec=None,
                )
            )

        return rows

    # ── Device matrix ────────────────────────────────────────
    async def device_matrix(self, *, days: int) -> list[DeviceMatrixRow]:
        """Counts grouped by ``(device_kind, decision)``."""
        since = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)
        # Coalesce null device_kind into "unknown" so the matrix has
        # a single explicit bucket rather than a sneaky null cell.
        device_kind = func.coalesce(
            PlaybackEvent.device_kind, "unknown"
        ).label("device_kind")
        stmt = (
            select(
                device_kind,
                PlaybackEvent.decision,
                func.count(PlaybackEvent.id).label("count"),
            )
            .where(PlaybackEvent.started_at >= since)
            .group_by(device_kind, PlaybackEvent.decision)
            .order_by(device_kind, PlaybackEvent.decision)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            DeviceMatrixRow(
                device_kind=r.device_kind,
                decision=r.decision,
                count=int(r.count),
            )
            for r in rows
        ]

    # ── Decision trend ───────────────────────────────────────
    async def decision_trend(self, *, days: int) -> list[DecisionDayRow]:
        """Daily counts per decision for a stacked sparkline."""
        since = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)
        # ``func.date(...)`` is portable across SQLite and Postgres
        # (Postgres returns a date; SQLite returns an ISO string).
        # We don't bind a Date column type — let the result come back
        # as whatever the driver yields and normalize in Python.
        day_expr = func.date(PlaybackEvent.started_at).label("day")
        stmt = (
            select(
                day_expr,
                PlaybackEvent.decision,
                func.count(PlaybackEvent.id).label("count"),
            )
            .where(PlaybackEvent.started_at >= since)
            .group_by(day_expr, PlaybackEvent.decision)
            .order_by(day_expr, PlaybackEvent.decision)
        )
        rows = (await self._session.execute(stmt)).all()
        out: list[DecisionDayRow] = []
        for r in rows:
            raw_day = r.day
            # Normalize: SQLite emits "YYYY-MM-DD" strings; Postgres
            # emits a date. Both convert cleanly.
            if isinstance(raw_day, str):
                day = _dt.date.fromisoformat(raw_day)
            elif isinstance(raw_day, _dt.datetime):
                day = raw_day.date()
            else:
                day = raw_day
            out.append(
                DecisionDayRow(
                    day=day,
                    decision=r.decision,
                    count=int(r.count),
                )
            )
        return out

    # ── Cursors ──────────────────────────────────────────────
    async def list_cursors(self) -> list[CursorRow]:
        """Every ``IntegrationPollingCursor`` with its integration's
        name + kind joined in for display."""
        stmt = (
            select(
                IntegrationPollingCursor,
                Integration.name.label("integration_name"),
                Integration.kind.label("integration_kind"),
            )
            .outerjoin(
                Integration,
                Integration.id == IntegrationPollingCursor.integration_id,
            )
            .order_by(IntegrationPollingCursor.updated_at.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            CursorRow(
                cursor=r[0],
                integration_name=r.integration_name,
                integration_kind=r.integration_kind,
            )
            for r in rows
        ]

    async def reset_cursors_for_integration(
        self, integration_id: str
    ) -> int:
        """Delete every cursor for ``integration_id``. Returns the
        number of rows deleted so the API can confirm."""
        cursors = (
            await self._session.execute(
                select(IntegrationPollingCursor).where(
                    IntegrationPollingCursor.integration_id == integration_id
                )
            )
        ).scalars().all()
        count = 0
        for c in cursors:
            await self._session.delete(c)
            count += 1
        if count:
            await self._session.flush()
        return count


__all__ = [
    "PlaybackFilter",
    "PlaybackEventRow",
    "PlaybackEventPage",
    "PlaybackStatsService",
    "TopTranscodedRow",
    "DeviceMatrixRow",
    "DecisionDayRow",
    "CursorRow",
]
