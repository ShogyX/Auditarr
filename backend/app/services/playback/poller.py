"""Playback telemetry poller (Stage 16).

For each enabled Plex/Jellyfin integration:

1. Look up the last polling cursor for ``cursor_kind="playback_events"``
2. Ask the integration's provider for events after that cursor
3. Apply the integration's configured path mappings to each event
4. Try to resolve the (remapped) ``source_path`` to a known
   :class:`MediaFile` row; record null when unresolved
5. Insert new ``PlaybackEvent`` rows (deduplicated by
   ``(integration_id, upstream_id)`` via the unique constraint)
6. Update the cursor to the latest ``started_at`` we saw
7. Compute drift over the batch and, if the result is concerning,
   stash a short health-detail update on the integration so the UI
   can prompt the operator to configure path mappings

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
from app.models.playback import IntegrationPollingCursor, PlaybackEvent
from app.utils.datetime import utcnow

log = get_logger("auditarr.playback.poller", category="playback")

CURSOR_KIND = "playback_events"


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
            }
            try:
                async with self._session.begin_nested():
                    row = PlaybackEvent(**row_dict)
                    self._session.add(row)
                    await self._session.flush()
                inserted += 1
                if latest_started_at is None or dto.started_at > latest_started_at:
                    latest_started_at = dto.started_at
            except IntegrityError:
                # Duplicate (integration_id, upstream_id) — the savepoint
                # rolled the bad insert back; carry on with the rest.
                pass

        outcome.inserted = inserted

        # Advance cursor if we made progress.
        if latest_started_at is not None:
            await self._upsert_cursor(integration.id, latest_started_at)

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

    def _parse_cursor(self, value: str) -> _dt.datetime | None:
        try:
            return _dt.datetime.fromisoformat(value)
        except ValueError:
            return None

    async def _resolve_paths(self, paths: list[str]) -> dict[str, str | None]:
        """Return ``{path: media_file_id-or-None}`` for each input path."""
        if not paths:
            return {}
        rows = await self._session.execute(
            select(MediaFile.path, MediaFile.id).where(MediaFile.path.in_(paths))
        )
        found = {row[0]: row[1] for row in rows.all()}
        return {p: found.get(p) for p in paths}


# Silence linters when sqlite_insert is unused — kept for future
# bulk-insert optimization.
_ = sqlite_insert
