"""Playback telemetry poller (Stage 16; cursor audit Stage 09).

For each enabled Plex/Jellyfin integration:

1. Look up the last polling cursor for ``cursor_kind="playback_events"``
2. Ask the integration's provider for events after that cursor
3. Apply the integration's configured path mappings to each event
4. Try to resolve the (remapped) ``source_path`` to a known
   :class:`MediaFile` row; record null when unresolved
5. Insert new ``PlaybackEvent`` rows (deduplicated by
   ``(integration_id, upstream_id)`` via the unique constraint)
6. Update the cursor to ``max(started_at) − safety_skew`` so
   slightly-out-of-order events on the next poll aren't dropped
   (Stage 09; see ``CURSOR_SAFETY_SKEW`` below)
7. Stash a short "last poll" health-detail line on the integration
   so the dashboard can surface "Last poll: N events ingested at T"
8. Compute drift over the batch and, if the result is concerning,
   replace the health-detail with a path-mappings hint instead

The poller is conservative: failures for one integration don't
propagate to others, and the cursor only advances if we successfully
inserted events.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.events.bus import EventBus, DomainEvent
from app.integrations.manager import IntegrationManager
from app.integrations.path_mapping import (
    DriftReport,
    PathMapping,
    parse_mappings,
    remap_path_chain,
)
from app.models.integration import Integration
from app.models.media import MediaFile
from app.models.path_mapping import GlobalPathMapping
from app.models.playback import (
    IntegrationPollingCursor,
    PlaybackEvent,
    PlaybackSession,
)
from app.models.playback_device import PlaybackDevice
from app.utils.datetime import utcnow

log = get_logger("auditarr.playback.poller", category="playback")

CURSOR_KIND = "playback_events"

# Stage 09 (v1.7) — the cursor advances to ``max(started_at) −
# CURSOR_SAFETY_SKEW`` rather than ``max(started_at)`` itself.
# Plex (and to a lesser extent Jellyfin) can return events with
# slightly-out-of-order ``started_at`` timestamps — a session
# that was being held in cache for transcoding decision data,
# for example, may write its started_at to the history page a
# few seconds after a session that started later. Without a
# safety skew, the cursor advances past the late-arriving
# event's started_at and the next poll's `since` filter drops it
# silently.
#
# 60 seconds covers all real-world cases we've seen. Replays are
# harmless thanks to the unique constraint on
# ``(integration_id, upstream_id)`` — the savepoint pattern
# below rolls back duplicates without disturbing the rest of
# the batch.
CURSOR_SAFETY_SKEW = _dt.timedelta(seconds=60)


@dataclass(slots=True)
class PollOutcome:
    integration_id: str
    fetched: int = 0
    inserted: int = 0
    resolved: int = 0
    unresolved: int = 0
    drift_suspected: bool = False
    error: str | None = None


class PlaybackPoller:
    """Polls one integration at a time. Stateless except for the DB.

    Construct once per worker tick, call :meth:`poll_one` per
    integration. The session and integration manager are injected so
    tests can swap mocks.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        manager: IntegrationManager,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._manager = manager
        self._bus = event_bus

    async def poll_one(self, integration: Integration) -> PollOutcome:
        outcome = PollOutcome(integration_id=integration.id)
        provider = self._manager.provider_for(integration.kind)
        if provider is None:
            outcome.error = f"no provider registered for kind={integration.kind!r}"
            return outcome

        # Read the cursor.
        cursor = await self._get_cursor(integration.id)
        since = self._parse_cursor(cursor.cursor_value) if cursor else None

        # Build the provider config (handles secret decryption).
        try:
            config = self._manager.build_config(integration)
        except Exception as exc:  # noqa: BLE001
            outcome.error = f"build_config failed: {exc}"
            return outcome

        try:
            dtos = await provider.fetch_playback_events(config, since)
        except NotImplementedError:
            # Provider hasn't implemented the optional method.
            return outcome
        except Exception as exc:  # noqa: BLE001
            outcome.error = str(exc)
            log.warning(
                "playback.poller.fetch_failed",
                integration_id=integration.id,
                error=str(exc),
            )
            return outcome

        outcome.fetched = len(dtos)
        if not dtos:
            # v1.9 Stage 6.2 — even on a zero-event poll, touch
            # the cursor so the dashboard's "Last polled" line
            # reflects current cadence. See the longer comment
            # on ``_touch_cursor``.
            await self._touch_cursor(integration.id)
            # v1.9 Stage 6.3 — also run the live-session merge.
            # Quiet history is exactly when live sessions matter:
            # short sessions, aborted sessions, currently-playing
            # streams that haven't crossed upstream's "watched"
            # threshold won't appear in history but ARE playback
            # events the operator wants logged. We need to build
            # config + look up mappings here since the
            # post-history block does that work below for the
            # non-empty case only.
            from sqlalchemy import select as _select

            ig_mappings = parse_mappings(
                integration.config.get("path_mappings")
            )
            global_rows = (
                await self._session.execute(
                    _select(GlobalPathMapping)
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
            synthesized = await self._merge_live_sessions(
                integration=integration,
                config=config,
                provider=provider,
                ig_mappings=ig_mappings,
                global_mappings=global_mappings,
            )
            outcome.inserted += synthesized
            outcome.fetched += synthesized
            # Zero-history poll cycles still need an explicit commit:
            # _touch_cursor and any rows synthesized by the live-merge
            # are otherwise discarded when the worker's session
            # closes. The non-empty path commits at the end of
            # poll_one; this branch returns early and must mirror it.
            await self._session.commit()
            return outcome

        # Apply path mappings.
        # Stage 5 (audit follow-up): chain per-integration mappings
        # then global mappings. The global layer is a fresh fetch
        # on every poll cycle — there are typically <10 rows so the
        # query is cheap; doing it per-cycle means a recently-added
        # global mapping takes effect on the next poll without
        # bouncing the worker.
        from sqlalchemy import select as _select

        ig_mappings = parse_mappings(integration.config.get("path_mappings"))
        global_rows = (
            await self._session.execute(
                _select(GlobalPathMapping)
                .where(GlobalPathMapping.enabled.is_(True))
                .order_by(
                    GlobalPathMapping.priority.asc(),
                    GlobalPathMapping.created_at.asc(),
                )
            )
        ).scalars().all()
        global_mappings = [
            PathMapping(src_prefix=r.from_path.rstrip("/"),
                        dst_prefix=r.to_path.rstrip("/"))
            for r in global_rows
            if r.from_path and r.to_path
        ]
        for dto in dtos:
            dto.source_path = remap_path_chain(
                dto.source_path, ig_mappings, global_mappings
            )

        # Resolve to MediaFile rows. We do a single batched IN query
        # rather than N point lookups.
        paths = list({dto.source_path for dto in dtos})
        resolved = await self._resolve_paths(paths)
        outcome.resolved = sum(1 for dto in dtos if resolved.get(dto.source_path))
        outcome.unresolved = outcome.fetched - outcome.resolved

        # Insert events. Dedup via the unique constraint —
        # ``(integration_id, upstream_id)`` — so a slightly-overlapping
        # poll window doesn't duplicate.
        #
        # We wrap each insert in a SAVEPOINT (``begin_nested``) so an
        # IntegrityError on a duplicate row only rolls back that one
        # row, not the whole session. A full session rollback would
        # detach every previously-added entity (including the
        # ``integration`` row passed in by the caller), and any
        # subsequent lazy attribute access would trigger the
        # SQLAlchemy "MissingGreenlet" diagnostic.
        inserted = 0
        latest_started_at: _dt.datetime | None = None
        for dto in dtos:
            # v1.9 OP-10 — find a matching SSE-tracked session.
            # When found, we still insert the event (caveat 4 —
            # preserve diagnosability) but tag it with the
            # session id so the analyzer can dedup at read time.
            matched_session_id = await self._find_matching_session(
                integration_id=integration.id,
                rating_key=dto.rating_key,
                started_at=dto.started_at,
            )

            row_dict = {
                "integration_id": integration.id,
                "media_file_id": resolved.get(dto.source_path),
                "source_path": dto.source_path,
                "device_kind": dto.device_kind,
                "device_name": dto.device_name,
                "decision": dto.decision,
                "reason_code": dto.reason_code,
                "source_codec": dto.source_codec,
                "source_bitrate_kbps": dto.source_bitrate_kbps,
                "source_width": dto.source_width,
                "source_height": dto.source_height,
                "source_container": dto.source_container,
                "target_codec": dto.target_codec,
                "target_bitrate_kbps": dto.target_bitrate_kbps,
                "started_at": dto.started_at,
                "completed_at": dto.completed_at,
                "duration_s": dto.duration_s,
                "upstream_id": dto.upstream_id,
                # v1.9 OP-10 — record the matched session id (or
                # None) so the analyzer can dedup primary-source
                # reads against the session table.
                "reconciled_with_session_id": matched_session_id,
            }
            try:
                async with self._session.begin_nested():
                    row = PlaybackEvent(**row_dict)
                    self._session.add(row)
                    await self._session.flush()
                inserted += 1
                if latest_started_at is None or dto.started_at > latest_started_at:
                    latest_started_at = dto.started_at
                # v1.9 Stage 9.1 — upsert the device row for
                # this event. Best-effort: a device upsert
                # failure doesn't fail the event insert.
                try:
                    await self._upsert_device(
                        integration_id=integration.id,
                        device_kind=dto.device_kind,
                        device_name=dto.device_name,
                        decision=dto.decision,
                        seen_at=dto.started_at,
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "playback.poller.device_upsert_failed",
                        integration_id=integration.id,
                    )
            except IntegrityError:
                # Duplicate (integration_id, upstream_id) — the savepoint
                # rolled the bad insert back; carry on with the rest.
                pass

            # v1.9 OP-10 — flag the matched session as
            # reconciled-with-history. Best-effort; failure
            # doesn't break the history scrape.
            if matched_session_id is not None:
                try:
                    await self._mark_session_reconciled_by_id(
                        matched_session_id
                    )
                except Exception:  # noqa: BLE001
                    pass

        outcome.inserted = inserted

        # Stage 09 — advance cursor to ``latest_started_at −
        # CURSOR_SAFETY_SKEW`` so slightly-out-of-order events
        # arriving on the next poll aren't dropped. Dedup is
        # handled by the unique constraint above; replays are
        # harmless. We only advance when something was inserted
        # (preserves the previous "no progress → no advance"
        # contract that protects against a transient empty
        # provider response stomping a known-good cursor).
        #
        # v1.9 Stage 6.2 — zero-event polls take the early
        # return above (which now also touches the cursor's
        # ``updated_at``); by the time we reach here, dtos was
        # non-empty and ``latest_started_at`` is set.
        if latest_started_at is not None:
            cursor_value = latest_started_at - CURSOR_SAFETY_SKEW
            await self._upsert_cursor(integration.id, cursor_value)

        # v1.9 Stage 6.3 — Live + history merge. The history
        # endpoint Plex/Jellyfin expose only writes a row when a
        # session crosses the upstream's "watched" threshold
        # (Plex default ~90%; Jellyfin similar). Aborted sessions,
        # quick-skips, brief samples — none of those land in
        # ``/status/sessions/history``, even though they ARE
        # playback events that affect transcode statistics.
        #
        # The merge: for each currently-live session the provider
        # reports, check whether it has crossed a "completed
        # enough" threshold (>= 30s elapsed since started_at OR
        # >= 90% viewOffset / progress_pct). If so, AND there is
        # no history row already for the session, synthesize a
        # PlaybackEvent with a stable upstream_id derived from
        # the live session id. Subsequent polls re-see the same
        # live session id and the unique constraint protects
        # against duplicates.
        #
        # Synthetic upstream_id format: "live:<session.upstream_id>"
        # — the "live:" prefix prevents collision with the
        # provider's own history-row upstream_ids (Plex's
        # ratingKey+viewedAt strings; Jellyfin's session GUIDs).
        synthesized = await self._merge_live_sessions(
            integration=integration,
            config=config,
            provider=provider,
            ig_mappings=ig_mappings,
            global_mappings=global_mappings,
        )
        outcome.inserted += synthesized
        outcome.fetched += synthesized

        # Drift detection.
        # Stage 5 (audit follow-up): "mappings configured" now means
        # EITHER per-integration OR global mappings — an operator
        # who's only set up global mappings shouldn't get nagged to
        # configure per-integration ones.
        drift = DriftReport(
            seen=outcome.fetched,
            resolved=outcome.resolved,
            has_mappings_configured=bool(ig_mappings) or bool(global_mappings),
        )
        outcome.drift_suspected = drift.drift_suspected
        if drift.drift_suspected:
            integration.health_status = "degraded"
            integration.health_detail = drift.detail()
            integration.health_checked_at = utcnow()
            if self._bus is not None:
                await self._bus.publish(
                    DomainEvent(
                        name="integration.path_drift",
                        source="playback.poller",
                        payload={
                            "integration_id": integration.id,
                            "seen": drift.seen,
                            "resolved": drift.resolved,
                            "has_mappings": bool(ig_mappings) or bool(global_mappings),
                        },
                    )
                )
        else:
            # Stage 09 (plan §481) — surface last-poll counts on
            # the integration so the dashboard can render "Last
            # poll: N events ingested at T". Only written when
            # there's no drift to surface; drift detail is
            # operator-actionable and wins the slot.
            #
            # We don't downgrade an existing ``healthy``/``ok``
            # status here — just refresh the detail string + the
            # checked_at timestamp. The operator's existing
            # healthcheck cron owns the status field.
            now = utcnow()
            integration.health_detail = _format_last_poll_detail(
                fetched=outcome.fetched,
                inserted=outcome.inserted,
                resolved=outcome.resolved,
                unresolved=outcome.unresolved,
                at=now,
            )
            integration.health_checked_at = now

        await self._session.commit()
        log.info(
            "playback.poller.polled",
            integration_id=integration.id,
            fetched=outcome.fetched,
            inserted=outcome.inserted,
            resolved=outcome.resolved,
            drift=outcome.drift_suspected,
        )
        return outcome

    # ── Helpers ────────────────────────────────────────────────
    async def _get_cursor(
        self, integration_id: str
    ) -> IntegrationPollingCursor | None:
        result = await self._session.execute(
            select(IntegrationPollingCursor).where(
                IntegrationPollingCursor.integration_id == integration_id,
                IntegrationPollingCursor.cursor_kind == CURSOR_KIND,
            )
        )
        return result.scalar_one_or_none()

    async def _upsert_cursor(
        self, integration_id: str, value: _dt.datetime
    ) -> None:
        existing = await self._get_cursor(integration_id)
        iso = value.isoformat()
        if existing is None:
            self._session.add(
                IntegrationPollingCursor(
                    integration_id=integration_id,
                    cursor_kind=CURSOR_KIND,
                    cursor_value=iso,
                )
            )
        else:
            existing.cursor_value = iso
            existing.updated_at = utcnow()

    async def _touch_cursor(self, integration_id: str) -> None:
        """v1.9 Stage 6.2 — touch the cursor's ``updated_at``
        without changing ``cursor_value``.

        Used after zero-event polls so the dashboard's
        "Last polled N ago" line reflects actual poll cadence,
        not the time of the most recent event-bearing poll. If
        no cursor row exists yet (first poll, no events ever
        seen), seed one with an empty cursor value so the row
        exists for the next poll to update.

        We use the integration's own start time as the seed
        ``cursor_value`` rather than ``utcnow()``: a fresh
        cursor at "now" would tell the next poll's ``since``
        filter to drop every event that arrives between now and
        the next tick, silently losing data. The empty-string
        sentinel forces the next event-bearing poll to write
        the real cursor.
        """
        existing = await self._get_cursor(integration_id)
        if existing is None:
            self._session.add(
                IntegrationPollingCursor(
                    integration_id=integration_id,
                    cursor_kind=CURSOR_KIND,
                    cursor_value="",
                )
            )
        else:
            existing.updated_at = utcnow()

    # ── v1.9 Stage 9.1 — device index upsert ────────────────────
    async def _upsert_device(
        self,
        *,
        integration_id: str,
        device_kind: str | None,
        device_name: str | None,
        decision: str,
        seen_at: _dt.datetime,
    ) -> None:
        """Upsert a PlaybackDevice row for the given (integration,
        client_key) pair and bump its decision-specific counter.

        ``client_key`` is derived from a stable hash of
        ``(device_kind, device_name)`` after normalizing each
        component. This is good enough for all current providers
        — Plex / Jellyfin emit consistent platform + name
        strings for the same physical device across sessions.
        When the providers grow stable client GUIDs (Stage 9
        follow-up), the key derivation switches to GUID-first.

        v1.9 audit fix (DEV-1): the hash includes ``name``, so a
        rename upstream produces a NEW client_key — and a new
        device row by design. Historical counters for the old
        name remain attached to the old row. This is correct
        behavior; we do NOT attempt to refresh the stored name
        on the matched row (the previous code did, but the
        branch was unreachable because the new hash never
        matched the old row).

        v1.9 audit fix (DEV-4): device_name is trimmed before
        hashing so " Living Room " and "Living Room" collapse
        into one device.

        v1.9 audit fix (DEV-2): the upsert runs inside its OWN
        ``begin_nested`` savepoint. A race-induced
        ``IntegrityError`` (two pollers inserting the same
        client_key concurrently) rolls back the savepoint
        cleanly rather than corrupting the parent transaction
        and breaking the rest of the poll cycle.

        When BOTH ``device_kind`` and ``device_name`` are None
        (after trimming), the upsert is a no-op — a row with
        all-empty identifiers carries no value and would
        conflate every "unknown device" event into one bucket.
        """
        # DEV-4: normalize identifiers (trim whitespace,
        # collapse empty strings to None).
        norm_kind = (device_kind or "").strip() or None
        norm_name = (device_name or "").strip() or None
        if norm_kind is None and norm_name is None:
            return

        client_key = _derive_client_key(norm_kind, norm_name)
        seen = _ensure_utc_aware(seen_at)

        # DEV-2: wrap the whole select-then-insert/update in a
        # savepoint so a race-induced IntegrityError doesn't
        # invalidate the outer transaction.
        try:
            async with self._session.begin_nested():
                result = await self._session.execute(
                    select(PlaybackDevice).where(
                        PlaybackDevice.integration_id == integration_id,
                        PlaybackDevice.client_key == client_key,
                    )
                )
                row = result.scalars().first()
                if row is None:
                    row = PlaybackDevice(
                        integration_id=integration_id,
                        client_key=client_key,
                        name=norm_name,
                        platform=norm_kind,
                        first_seen_at=seen,
                        last_seen_at=seen,
                        playback_count=0,
                        transcode_count=0,
                        direct_play_count=0,
                        direct_stream_count=0,
                    )
                    self._session.add(row)
                    await self._session.flush()
                else:
                    existing_last = _ensure_utc_aware(row.last_seen_at)
                    if seen > existing_last:
                        row.last_seen_at = seen
                    # first_seen_at: only move backward.
                    existing_first = _ensure_utc_aware(row.first_seen_at)
                    if seen < existing_first:
                        row.first_seen_at = seen

                row.playback_count += 1
                if decision == "transcode":
                    row.transcode_count += 1
                elif decision == "direct_stream":
                    row.direct_stream_count += 1
                elif decision == "direct_play":
                    row.direct_play_count += 1
                # Other decisions (e.g. "failed") still increment
                # playback_count but no decision-specific bucket
                # — they represent attempted-but-not-completed
                # plays.
        except IntegrityError:
            # Concurrent insert race — another coroutine wrote
            # the row first. The savepoint rolled our INSERT
            # back. Their row already has the upstream's
            # counter increment if they got here first, so we
            # don't double-increment. Best-effort by design.
            pass

    # ── v1.9 Stage 6.3 — live + history merge ────────────────────
    # Threshold past which a live session is "completed enough"
    # to be worth recording even if upstream history hasn't
    # caught up yet. Either ≥30s elapsed OR ≥90% progress.
    _LIVE_MERGE_MIN_ELAPSED = _dt.timedelta(seconds=30)
    _LIVE_MERGE_MIN_PROGRESS_PCT = 90.0

    async def _merge_live_sessions(
        self,
        *,
        integration: Integration,
        config,
        provider,
        ig_mappings: list[PathMapping],
        global_mappings: list[PathMapping],
    ) -> int:
        """Fetch live sessions, synthesize PlaybackEvent rows for
        the ones that have crossed the "completed enough"
        threshold but don't yet have a history row.

        Returns the number of synthesized rows inserted.

        Best-effort: any exception inside (provider doesn't
        implement ``fetch_live_playbacks``, transient network,
        DB constraint violation on a race) logs and returns 0.
        The history portion of the poll has already committed
        its rows so a failure here doesn't lose anything.
        """
        if not hasattr(provider, "fetch_live_playbacks"):
            return 0
        try:
            lives = await provider.fetch_live_playbacks(config)
        except NotImplementedError:
            return 0
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "playback.poller.live_fetch_failed",
                integration_id=integration.id,
                error=str(exc),
            )
            return 0
        if not lives:
            return 0

        # Apply path mappings to live sessions' source_path the
        # same way history events get remapped — synthesized
        # PlaybackEvent rows should sit in Auditarr-side path
        # space.
        for live in lives:
            live.source_path = remap_path_chain(
                live.source_path, ig_mappings, global_mappings
            )

        now = utcnow()
        eligible = [
            live for live in lives
            if self._live_session_eligible(live, now=now)
        ]
        if not eligible:
            return 0

        # Look up existing PlaybackEvent rows for these
        # synthetic upstream_ids in one batched IN query.
        synthetic_ids = [
            f"live:{live.upstream_id}" for live in eligible
        ]
        existing_rows = await self._session.execute(
            select(PlaybackEvent.upstream_id)
            .where(PlaybackEvent.integration_id == integration.id)
            .where(PlaybackEvent.upstream_id.in_(synthetic_ids))
        )
        existing_set = {row[0] for row in existing_rows.all()}

        # Resolve paths in one batch.
        paths_to_resolve = list({live.source_path for live in eligible})
        resolved = await self._resolve_paths(paths_to_resolve)

        inserted_count = 0
        for live in eligible:
            synth_id = f"live:{live.upstream_id}"
            if synth_id in existing_set:
                continue
            try:
                # Wrap each synthesized insert in a SAVEPOINT so a
                # unique-constraint race (concurrent poller or the
                # history scrape arriving just before us) only rolls
                # back this one row. The historical-events branch
                # above uses the same begin_nested() pattern; this
                # call site originally documented but did not
                # implement it, and the bare ``self._session.rollback()``
                # in the except path was wiping the entire outer
                # transaction — including any history rows already
                # inserted in this poll cycle.
                async with self._session.begin_nested():
                    self._session.add(
                        PlaybackEvent(
                            integration_id=integration.id,
                            upstream_id=synth_id,
                            media_file_id=resolved.get(live.source_path),
                            source_path=live.source_path,
                            decision=live.decision,
                            started_at=live.started_at,
                            device_kind=live.device_kind,
                            device_name=live.device_name,
                            source_codec=live.source_codec,
                            source_bitrate_kbps=live.source_bitrate_kbps,
                            source_width=live.source_width,
                            source_height=live.source_height,
                        )
                    )
                    await self._session.flush()
                inserted_count += 1
                # v1.9 Stage 9.1 — upsert device for live merge too.
                try:
                    await self._upsert_device(
                        integration_id=integration.id,
                        device_kind=live.device_kind,
                        device_name=live.device_name,
                        decision=live.decision,
                        seen_at=live.started_at,
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "playback.poller.device_upsert_failed",
                        integration_id=integration.id,
                    )
            except IntegrityError:
                # Duplicate (integration_id, upstream_id) — the
                # savepoint already rolled the bad insert back;
                # carry on with the rest of the batch.
                pass
        return inserted_count

    def _live_session_eligible(
        self, live, *, now: _dt.datetime
    ) -> bool:
        """A live session is eligible for history synthesis if
        EITHER it has been playing >= 30 seconds OR progress is
        >= 90%. The 30-second floor catches direct-play sessions
        the dashboard cares about (someone actually watching);
        the 90% ceiling catches near-finished sessions that
        will write to history any moment now but haven't yet.
        """
        # Elapsed time floor.
        if live.started_at is not None:
            # Strip tz info defensively for the subtraction. live
            # DTOs from Plex / Jellyfin carry tz-aware datetimes,
            # but a misbehaving provider could return naive.
            started = live.started_at
            now_aware = now
            if started.tzinfo is None and now_aware.tzinfo is not None:
                now_aware = now_aware.replace(tzinfo=None)
            elif started.tzinfo is not None and now_aware.tzinfo is None:
                started = started.replace(tzinfo=None)
            elapsed = now_aware - started
            if elapsed >= self._LIVE_MERGE_MIN_ELAPSED:
                return True
        # Progress ceiling.
        if (
            live.progress_pct is not None
            and live.progress_pct >= self._LIVE_MERGE_MIN_PROGRESS_PCT
        ):
            return True
        return False

    # v1.9 OP-10 — reconciliation window. Plex history's
    # ``viewedAt`` is rounded to the second; the SSE writer's
    # ``started_at`` is the wall-clock time we first observed
    # the session. ±5 minutes covers ordinary clock skew + the
    # time the operator paused/resumed before the play counted
    # toward the watch threshold. Configurable via env if we
    # find this needs tuning in production.
    _RECONCILE_WINDOW_SECONDS = 5 * 60

    async def _find_matching_session(
        self,
        *,
        integration_id: str,
        rating_key: str | None,
        started_at: _dt.datetime,
    ) -> str | None:
        """v1.9 OP-10 — locate the SSE-tracked PlaybackSession
        that matches this history DTO.

        Returns the session's ``id`` if a match is found, else
        None. Caveats addressed:

          * **Caveat 11 (rating_key NULL guard)**: providers that
            don't expose a rating_key (Jellyfin today) emit
            ``rating_key=None`` on their DTOs. We explicitly
            require both sides to have a non-None rating_key
            before matching, so a Jellyfin DTO never accidentally
            matches a Plex session that happened to overlap in
            time.
          * **Caveat 3 (closest match)**: with two sessions in
            the same ±5 min window (e.g. operator watched the
            same episode twice in quick succession), pick the
            one whose ``started_at`` is closest to the DTO's
            ``started_at`` by absolute delta. The composite
            index ``ix_playback_sessions_recon`` covers the
            equality columns; the trailing range scan over
            ``started_at`` is small in practice.
          * **Caveat 4 (don't skip insert)**: this function ONLY
            returns the matched id. The caller still inserts the
            PlaybackEvent and tags it with the id — preserves
            full diagnosability.
        """
        if not rating_key:
            return None
        window = _dt.timedelta(seconds=self._RECONCILE_WINDOW_SECONDS)
        result = await self._session.execute(
            select(PlaybackSession.id, PlaybackSession.started_at).where(
                PlaybackSession.integration_id == integration_id,
                PlaybackSession.rating_key == rating_key,
                PlaybackSession.rating_key.is_not(None),
                PlaybackSession.started_at >= started_at - window,
                PlaybackSession.started_at <= started_at + window,
            )
        )
        candidates = list(result.all())
        if not candidates:
            return None
        # Caveat 3: pick the closest by |delta|.
        best_id: str | None = None
        best_delta: _dt.timedelta | None = None
        # Some DB drivers return naive datetimes for SQLite — be
        # defensive and coerce both sides to UTC-aware so the
        # subtraction doesn't raise.
        target = (
            started_at
            if started_at.tzinfo is not None
            else started_at.replace(tzinfo=_dt.UTC)
        )
        for row_id, row_started in candidates:
            cand = (
                row_started
                if row_started.tzinfo is not None
                else row_started.replace(tzinfo=_dt.UTC)
            )
            delta = abs(cand - target)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_id = row_id
        return best_id

    async def _mark_session_reconciled_by_id(
        self, session_id: str
    ) -> None:
        """v1.9 OP-10 — flag a specific session as
        history-reconciled. Looked up by primary key (the
        ``_find_matching_session`` helper returned this id), so
        the update is a single point read."""
        from app.models.playback import PlaybackSession

        result = await self._session.execute(
            select(PlaybackSession).where(PlaybackSession.id == session_id)
        )
        row = result.scalars().first()
        if row is not None:
            row.reconciled_with_history = True

    async def _mark_session_reconciled(
        self, integration_id: str, started_at: _dt.datetime
    ) -> None:
        """v1.8.0 (Stage 17) reconciliation hook.

        When the history scrape ingests an event for a session
        the SSE listener also recorded (matched by
        ``(integration_id, started_at within ±60s)``), mark the
        PlaybackSession row's ``reconciled_with_history`` flag.

        Best-effort: failure is silent.
        """
        # Avoid circular import.
        from app.models.playback import PlaybackSession

        # Match within ±60 seconds because Plex's history
        # ``viewedAt`` timestamp may round to the nearest second
        # while our SSE-driven ``started_at`` is sub-second.
        window = _dt.timedelta(seconds=60)
        result = await self._session.execute(
            select(PlaybackSession).where(
                PlaybackSession.integration_id == integration_id,
                PlaybackSession.started_at >= started_at - window,
                PlaybackSession.started_at <= started_at + window,
                PlaybackSession.reconciled_with_history.is_(False),
            )
        )
        row = result.scalars().first()
        if row is not None:
            row.reconciled_with_history = True

    def _parse_cursor(self, value: str) -> _dt.datetime | None:
        # v1.9 Stage 6.2 — ``_touch_cursor`` seeds new rows with
        # an empty-string ``cursor_value`` so the row exists for
        # the dashboard's "Last polled" line. Treat empty as
        # "no cursor yet" so the next poll's ``since`` filter
        # is None (fetch full history once), not the epoch.
        if not value:
            return None
        try:
            return _dt.datetime.fromisoformat(value)
        except ValueError:
            return None

    async def _resolve_paths(self, paths: list[str]) -> dict[str, str | None]:
        """Return ``{path: media_file_id-or-None}`` for each input path."""
        return await resolve_media_paths(self._session, paths)


async def resolve_media_paths(
    session: AsyncSession, paths: list[str]
) -> dict[str, str | None]:
    """v1.9 OP-10 — module-level path → media_file_id resolver.

    Factored out of ``PlaybackPoller._resolve_paths`` so the SSE
    writer (``SessionStateManager``) can reuse the same lookup
    when populating ``media_file_id`` on a new session row.

    Returns ``{path: media_file_id-or-None}`` for each input path.
    Unknown paths map to None — the caller decides whether NULL
    is acceptable (it is, for SSE sessions where the upstream
    references a file Auditarr hasn't scanned yet).
    """
    if not paths:
        return {}
    rows = await session.execute(
        select(MediaFile.path, MediaFile.id).where(MediaFile.path.in_(paths))
    )
    found = {row[0]: row[1] for row in rows.all()}
    return {p: found.get(p) for p in paths}


async def resolve_media_path(
    session: AsyncSession, path: str
) -> str | None:
    """Single-row convenience wrapper around ``resolve_media_paths``."""
    result = await resolve_media_paths(session, [path])
    return result.get(path)


# Silence linters when sqlite_insert is unused — kept for future
# bulk-insert optimization.
_ = sqlite_insert


def _format_last_poll_detail(
    *,
    fetched: int,
    inserted: int,
    resolved: int,
    unresolved: int,
    at: _dt.datetime,
) -> str:
    """Stage 09 (plan §481) — short, operator-readable summary
    written to ``Integration.health_detail`` after a successful
    poll. Format keeps both the count and the resolved/unresolved
    split visible so an operator with a path-mapping problem
    sees the symptom without opening the path-mappings panel.

    Truncates to a reasonable length so the dashboard tooltip
    doesn't blow up.
    """
    # Render time as ISO seconds (no microseconds) for stable
    # display.
    when = at.replace(microsecond=0).isoformat()
    if fetched == 0:
        return f"Last poll: no events at {when}"
    if unresolved == 0:
        return (
            f"Last poll: {inserted} of {fetched} event"
            f"{'s' if fetched != 1 else ''} ingested at {when}"
        )
    # When some events couldn't be resolved, surface the split
    # so the operator sees the path-mappings gap.
    return (
        f"Last poll: {inserted} of {fetched} event"
        f"{'s' if fetched != 1 else ''} ingested at {when} "
        f"({resolved} resolved, {unresolved} unresolved — "
        f"check path mappings)"
    )


def _derive_client_key(
    device_kind: str | None, device_name: str | None
) -> str:
    """v1.9 Stage 9.1 — deterministic client key from the
    upstream's (kind, name) pair. SHA-1 is good enough — we
    aren't using this for security, just for uniqueness, and a
    rare collision would merge two distinct devices' stats
    (annoying but not catastrophic). Truncate to 16 hex chars
    for compact storage.

    v1.9 audit fix (DEV-4): trim and lowercase nothing — the
    callers (``_upsert_device``) trim before calling, but if a
    different caller invokes this directly we still trim for
    safety. Lowercasing would conflate "Bedroom TV" and
    "bedroom tv" which the upstream considers distinct devices,
    so we preserve case."""
    import hashlib

    kind = (device_kind or "").strip()
    name = (device_name or "").strip()
    composite = f"{kind}::{name}"
    # SHA-1 here generates a stable short id for a (kind, name) pair
    # — not a cryptographic identity proof. ``usedforsecurity=False``
    # silences the SAST flag without changing the digest.
    return hashlib.sha1(
        composite.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:16]


def _ensure_utc_aware(value: _dt.datetime) -> _dt.datetime:
    """SQLite returns tz-naive datetimes when reading back rows
    written with tz-aware values (the storage layer drops tzinfo).
    Coerce on read so subsequent comparisons don't raise."""
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.UTC)
    return value
