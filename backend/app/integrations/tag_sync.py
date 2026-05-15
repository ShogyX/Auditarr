"""Integration tag-sync ingestion.

Pulls :class:`TagSync` rows from an integration provider and reconciles
them into the ``media_tags`` table.

Tags from an integration are scoped by ``source`` (e.g. ``'sonarr'``,
``'radarr'``, ``'bazarr'``). On every sync we:

1. Compute the desired set of ``(media_file_id, name)`` pairs for this
   ``source`` by joining each :class:`TagSync` against ``media_files``
   whose ``path`` starts with the title path.
2. Drop existing rows for ``source`` that are no longer desired.
3. Insert any pairs that aren't already present.

This keeps the tag table consistent with upstream truth without
double-writing on every poll.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.events.bus import EventBus
from app.integrations.types import TagSync
from app.models.integration import Integration
from app.models.media import MediaFile
from app.models.tag import MediaTag

log = get_logger("auditarr.integrations.tags", category="integrations")


@dataclass(slots=True)
class TagSyncReport:
    integration_id: str
    inserted: int
    removed: int
    skipped_no_path: int
    title_count: int


class IntegrationTagSync:
    """Reconciles TagSync rows from a provider into media_tags."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._bus = event_bus

    async def apply(
        self, integration: Integration, tags: list[TagSync]
    ) -> TagSyncReport:
        """Reconcile ``tags`` against the DB for this integration's kind.

        ``integration.kind`` is the ``source`` we own. Tags from other
        sources are left alone.
        """
        source = integration.kind
        skipped_no_path = sum(1 for t in tags if not t.media_path)

        # Resolve each title path to its set of media_file ids.
        # Using LIKE with ``path/`` keeps the prefix match anchored at a
        # directory boundary so ``/data/tv/Show A`` doesn't accidentally
        # match ``/data/tv/Show Anniversary``.
        desired: set[tuple[str, str]] = set()
        for entry in tags:
            if not entry.media_path:
                continue
            prefix = os.fspath(entry.media_path).rstrip("/") + "/"
            result = await self._session.execute(
                select(MediaFile.id).where(MediaFile.path.like(f"{prefix}%"))
            )
            for (file_id,) in result.all():
                desired.add((file_id, entry.tag))

        # Existing rows for this source.
        existing_rows = (
            await self._session.execute(
                select(MediaTag).where(MediaTag.source == source)
            )
        ).scalars().all()
        existing: dict[tuple[str, str], MediaTag] = {
            (row.media_file_id, row.name): row for row in existing_rows
        }
        existing_keys = set(existing.keys())

        to_insert = desired - existing_keys
        to_remove_keys = existing_keys - desired

        for file_id, name in to_insert:
            self._session.add(
                MediaTag(media_file_id=file_id, name=name, source=source)
            )

        if to_remove_keys:
            # Use bulk delete keyed by id to avoid loading each row again.
            ids_to_remove = [existing[k].id for k in to_remove_keys]
            await self._session.execute(
                delete(MediaTag).where(MediaTag.id.in_(ids_to_remove))
            )

        await self._session.flush()

        report = TagSyncReport(
            integration_id=integration.id,
            inserted=len(to_insert),
            removed=len(to_remove_keys),
            skipped_no_path=skipped_no_path,
            title_count=len({t.media_path for t in tags if t.media_path}),
        )
        log.info(
            "integration.tag_sync",
            integration_id=integration.id,
            kind=integration.kind,
            inserted=report.inserted,
            removed=report.removed,
            titles=report.title_count,
        )
        if self._bus is not None:
            await self._bus.emit(
                "integration.tags_synced",
                {
                    "integration_id": integration.id,
                    "kind": integration.kind,
                    "inserted": report.inserted,
                    "removed": report.removed,
                },
                source="integrations",
            )
        return report
