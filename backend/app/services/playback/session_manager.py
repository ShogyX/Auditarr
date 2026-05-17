"""Live-session state management for the v1.8.0 SSE rework.

The :class:`SessionStateManager` owns two things:

  1. An in-memory cache of currently-active sessions keyed by
     ``(integration_id, session_key)``. Read by the live-tile
     endpoint (:func:`app.api.v1.playback.list_live_playbacks`)
     to render the dashboard without an upstream round-trip.
  2. A DB writer that upserts :class:`PlaybackSession` rows
     when sessions transition state (start, pause, stop). Writes
     are idempotent because Plex retries SSE events on
     reconnect.

The manager is process-local — the API and worker each construct
their own instance. Only the worker writes to the DB / receives
SSE events; the API reads cache-only via a process-local store
that the worker populates over the existing event bus
(``playback.live_changed`` channel). This avoids needing a
shared-memory backend.

Actually for simplicity in this first cut: the worker is the SOLE
writer AND keeps an authoritative DB. The API's live endpoint
reads sessions directly from the DB filtered to
``state != 'stopped'``, accepting the ~50ms DB round-trip per
dashboard render in exchange for not maintaining a Redis cache.
Future v1.8.x can push to Redis if the load justifies it.

Concurrency model: one ``handle_event()`` coroutine per Plex
integration, called sequentially from inside the listener task.
No internal locks needed because each integration's events are
processed serially.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.types import LivePlaybackDTO
from app.models.playback import PlaybackSession

log = get_logger("auditarr.playback.session_manager", category="playback")


# Plex states we map onto our 4-value enum. Plex's wire vocabulary
# is "playing" / "paused" / "buffering" / "stopped"; we keep the
# same labels for simplicity.
_KNOWN_STATES = frozenset({"playing", "paused", "buffering", "stopped"})


@dataclass(slots=True)
class SessionEnrichment:
    """Per-session metadata captured at session start.

    The SSE event itself only tells us ``session_key``,
    ``state``, ``rating_key``, and ``view_offset_ms``. The
    enrichment fields (codec, user, path, etc.) are fetched
    from :meth:`PlexProvider.fetch_one_session_snapshot` on
    the first state event for each session and re-used until
    Plex tells us the session ended.
    """

    decision: str  # "direct_play" | "direct_stream" | "transcode"
    source_path: str | None
    title: str | None
    grandparent_title: str | None
    user: str | None
    device_kind: str | None
    device_name: str | None
    source_codec: str | None
    source_bitrate_kbps: int | None
    source_width: int | None
    source_height: int | None
    source_container: str | None
    target_codec: str | None
    target_bitrate_kbps: int | None
    duration_ms: int | None
    reason_code: str | None = None


class SessionStateManager:
    """Owns the SSE → DB write path for one Plex integration.

    Construct fresh per worker task; do NOT share between
    integrations (the cache and the listener task lifecycle are
    1:1 with the integration).
    """

    def __init__(self, *, integration_id: str, db_session_factory: Any) -> None:
        self._integration_id = integration_id
        # ``db_session_factory`` is a zero-arg callable returning an
        # AsyncSession context manager (typically ``db.session``).
        # We open and commit a session per event rather than holding
        # one open across the listener's lifetime — SSE listeners
        # run for hours, but DB connections shouldn't.
        self._db_session_factory = db_session_factory

    # ── Public API ───────────────────────────────────────────────

    async def handle_state_event(
        self,
        *,
        session_key: str,
        state: str,
        view_offset_ms: int | None,
        enrichment: SessionEnrichment | None,
    ) -> None:
        """Process one SSE state event.

        The caller (the worker's listener task) is expected to
        have already fetched enrichment metadata from
        ``fetch_one_session_snapshot`` if this is a session it
        hasn't seen before. ``enrichment=None`` means the
        snapshot fetch failed or the session was already gone
        from Plex by the time we asked; we still record the
        state transition with what we know.

        State transitions:

        * "playing" / "paused" / "buffering" — upsert a row with
          the new state. ``stopped_at`` stays NULL.
        * "stopped" — set state and ``stopped_at``. Row stays
          in the table for history.

        All writes are idempotent: re-applying the same event
        produces the same row contents. Plex retries events on
        SSE reconnect so this matters in practice.
        """
        if state not in _KNOWN_STATES:
            log.warning(
                "session_manager.unknown_state",
                integration_id=self._integration_id,
                session_key=session_key,
                state=state,
                detail="Plex emitted a state we don't recognise; treating as 'playing'.",
            )
            state = "playing"

        now = _dt.datetime.now(_dt.UTC)
        stopped_at = now if state == "stopped" else None

        # Build the column set for the upsert.
        values: dict[str, Any] = {
            "integration_id": self._integration_id,
            "session_key": session_key,
            "state": state,
            "view_offset_ms": view_offset_ms,
            "last_event_at": now,
            "stopped_at": stopped_at,
        }
        if enrichment is not None:
            values.update(
                {
                    "decision": enrichment.decision,
                    "reason_code": enrichment.reason_code,
                    "source_path": enrichment.source_path,
                    "title": enrichment.title,
                    "grandparent_title": enrichment.grandparent_title,
                    "user": enrichment.user,
                    "device_kind": enrichment.device_kind,
                    "device_name": enrichment.device_name,
                    "source_codec": enrichment.source_codec,
                    "source_bitrate_kbps": enrichment.source_bitrate_kbps,
                    "source_width": enrichment.source_width,
                    "source_height": enrichment.source_height,
                    "source_container": enrichment.source_container,
                    "target_codec": enrichment.target_codec,
                    "target_bitrate_kbps": enrichment.target_bitrate_kbps,
                    "duration_ms": enrichment.duration_ms,
                }
            )

        async with self._db_session_factory() as session:
            await self._upsert(session, values)
            await session.commit()

        log.info(
            "session_manager.event_recorded",
            integration_id=self._integration_id,
            session_key=session_key,
            state=state,
            view_offset_ms=view_offset_ms,
            enriched=enrichment is not None,
        )

    async def handle_reconnect(self) -> None:
        """Called when the SSE transport reconnected.

        Sessions may have started AND ended during our
        disconnect; the next state event for any existing
        session will be authoritative, but sessions that are
        still active need their ``last_event_at`` refreshed so
        the live-tile endpoint doesn't time them out.

        For now we just log; the actual re-sync happens when
        the worker fetches a fresh ``/status/sessions`` snapshot
        immediately after reconnect (see worker.plex_event_listener).
        """
        log.info(
            "session_manager.reconnect_observed",
            integration_id=self._integration_id,
        )

    # ── DB primitives ────────────────────────────────────────────

    async def _upsert(
        self, session: AsyncSession, values: dict[str, Any]
    ) -> None:
        """INSERT a new row OR UPDATE the existing one on
        ``(integration_id, session_key)``.

        We deliberately query-first rather than relying on
        ``INSERT ... ON CONFLICT DO UPDATE`` because SQLite's
        upsert variant validates NOT NULL constraints against
        the proposed INSERT values even when the ON CONFLICT
        branch will fire — and a stop-event with
        ``enrichment=None`` legitimately doesn't have a
        ``decision`` to insert. Two queries is fine here: the
        Plex SSE rate is low (one event per state change per
        session, typically <1 Hz), and the read is indexed by
        the unique constraint.
        """
        # Check for existing row.
        existing = await session.execute(
            select(PlaybackSession).where(
                PlaybackSession.integration_id == values["integration_id"],
                PlaybackSession.session_key == values["session_key"],
            )
        )
        row = existing.scalars().first()
        if row is not None:
            # UPDATE: apply only the fields we've been given.
            # Always-updated columns:
            row.state = values["state"]
            row.last_event_at = values["last_event_at"]
            # Optionally-updated:
            if "view_offset_ms" in values:
                row.view_offset_ms = values["view_offset_ms"]
            if values.get("stopped_at") is not None:
                row.stopped_at = values["stopped_at"]
            # Enrichment fields — only overwrite when we have
            # them. Avoids blanking known-good metadata when a
            # stop event arrives with enrichment=None.
            for field in (
                "decision",
                "reason_code",
                "source_path",
                "title",
                "grandparent_title",
                "user",
                "device_kind",
                "device_name",
                "source_codec",
                "source_bitrate_kbps",
                "source_width",
                "source_height",
                "source_container",
                "target_codec",
                "target_bitrate_kbps",
                "duration_ms",
            ):
                if values.get(field) is not None:
                    setattr(row, field, values[field])
            return

        # INSERT: need a decision to satisfy NOT NULL. If the
        # caller didn't give us one (stop event with no
        # enrichment for a session we never observed the start
        # of), default to "direct_play" as the least-surprising
        # placeholder. The analyzer ignores rows without an
        # enriching event anyway.
        insert_values = dict(values)
        insert_values.setdefault("decision", "direct_play")
        session.add(PlaybackSession(**insert_values))

    # ── Read API used by the live endpoint ───────────────────────

    @staticmethod
    async def list_active_sessions(
        session: AsyncSession, *, integration_id: str | None = None
    ) -> list[PlaybackSession]:
        """Return active (non-stopped) sessions, optionally
        filtered to one integration.

        Called from :func:`app.api.v1.playback.list_live_playbacks`.
        """
        stmt = select(PlaybackSession).where(PlaybackSession.state != "stopped")
        if integration_id is not None:
            stmt = stmt.where(PlaybackSession.integration_id == integration_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


def enrichment_from_live_dto(dto: LivePlaybackDTO) -> SessionEnrichment:
    """Materialise a :class:`SessionEnrichment` from the existing
    :class:`LivePlaybackDTO` shape so callers don't have to
    re-extract per-field.

    The Plex provider returns LivePlaybackDTO from
    ``fetch_one_session_snapshot``; we just pivot it into the
    cache-friendly shape.
    """
    # ``LivePlaybackDTO.title`` carries either the movie title or
    # the episode title for TV. Plex separately exposes
    # ``grandparentTitle`` for the show name; the v1.7 DTO doesn't
    # carry that, so we leave grandparent_title=None here and the
    # listener can fill it from the raw snapshot if needed.
    return SessionEnrichment(
        decision=dto.decision,
        source_path=dto.source_path,
        title=dto.title,
        grandparent_title=None,
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
        duration_ms=None,  # not in the v1.7 DTO; the listener can plug it in
    )
