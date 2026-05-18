"""Operator-initiated media file deletion (v1.9 Stage 2.4).

Distinct from the rule-engine's hard-delete path in
``rules_service._hard_delete_media``:

- The rule path is triggered automatically by a matched Delete
  action; the actor is the rules engine (``actor_label="rules"``).
- This service is triggered by a human admin clicking Delete on the
  Files page; the actor is that admin (``actor_id`` set on every
  audit entry).

Both honour the same "trash means recoverable" promise, but this
service uses a date-bucketed trash layout (``trash/YYYY-MM-DD/<uuid>/
<original-relative-path>``) so an operator browsing trash can
distinguish what they removed when, and a Stage 2.6 factory-reset
can purge it cleanly.

Two modes:
  - ``remove_from_disk=False`` (default, safe): index-only delete.
    The ``MediaFile`` row is removed but the file on disk is
    untouched. The next scan will re-index it unless the operator
    has set up a path-rule disposition to keep it out.
  - ``remove_from_disk=True`` (destructive): the file is moved to
    the trash directory before the row is deleted.

Each deletion emits ``media.deleted`` so the UI's
``invalidateRelated(qc, "media")`` graph refreshes immediately.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.events.types import MEDIA_DELETED
from app.services.audit_service import AuditService
from app.services.repositories import MediaRepository

if TYPE_CHECKING:
    from app.core.settings import Settings
    from app.events.bus import EventBus
    from app.models.media import MediaFile
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("auditarr.media.delete", category="media")


@dataclass(slots=True)
class DeleteResult:
    """Per-file outcome of an operator delete."""

    media_id: str
    path: str
    """Original on-disk path at the time of delete."""
    removed_from_disk: bool
    """True iff the file was moved to the trash directory."""
    trash_path: str | None
    """Where the file landed inside the trash dir, if moved.

    ``None`` for index-only deletes and for files that were already
    missing on disk when the delete ran (which is logged as a
    warning, not a failure — the operator wanted the row gone, and
    the row is gone).
    """


class MediaDeleteService:
    """Operator-initiated single + bulk file deletion."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._bus = event_bus
        self._media = MediaRepository(session)
        self._audit = AuditService(session)

    async def delete_one(
        self,
        media_id: str,
        *,
        actor_id: str | None,
        remove_from_disk: bool,
        reason: str | None,
    ) -> DeleteResult:
        """Delete a single media file. Raises ``LookupError`` if the
        ``media_id`` is unknown — the router translates that to 404."""
        record = await self._media.get(media_id)
        if record is None:
            raise LookupError(media_id)
        return await self._delete_record(
            record,
            actor_id=actor_id,
            remove_from_disk=remove_from_disk,
            reason=reason,
            bucket_dir=None,
        )

    async def bulk_delete(
        self,
        media_ids: list[str],
        *,
        actor_id: str | None,
        remove_from_disk: bool,
        reason: str | None,
    ) -> list[DeleteResult]:
        """Delete a list of media files. Each id is processed
        independently; an unknown id is silently skipped (the caller
        will see fewer results than ids supplied, which is the same
        signal a row that's already gone produces).

        All disk-side deletes for one bulk call share a single trash
        bucket so the operator can recover the whole batch by moving
        one directory.
        """
        bucket_dir = (
            self._allocate_bucket_dir() if remove_from_disk else None
        )
        results: list[DeleteResult] = []
        for media_id in media_ids:
            record = await self._media.get(media_id)
            if record is None:
                # Silently skip — see docstring.
                continue
            results.append(
                await self._delete_record(
                    record,
                    actor_id=actor_id,
                    remove_from_disk=remove_from_disk,
                    reason=reason,
                    bucket_dir=bucket_dir,
                )
            )
        return results

    # ── Internals ──────────────────────────────────────────────

    def _allocate_bucket_dir(self) -> Path:
        """Allocate a fresh ``trash/YYYY-MM-DD/<uuid>/`` directory.

        We allocate once per bulk operation (or per single delete)
        so all files from the same operator click cluster together.
        The uuid keeps two concurrent operators from sharing a
        bucket — important when they have overlapping
        ``relative_path`` values.
        """
        today = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")
        bucket = (
            Path(self._settings.data_dir)
            / "trash"
            / today
            / str(uuid.uuid4())
        )
        bucket.mkdir(parents=True, exist_ok=True)
        return bucket

    async def _delete_record(
        self,
        record: MediaFile,
        *,
        actor_id: str | None,
        remove_from_disk: bool,
        reason: str | None,
        bucket_dir: Path | None,
    ) -> DeleteResult:
        trash_path: Path | None = None
        moved = False

        if remove_from_disk:
            bucket = bucket_dir or self._allocate_bucket_dir()
            src = Path(record.path)
            if src.exists():
                # Preserve the file's library-relative path inside
                # the bucket so the operator can find what they
                # removed by name. ``relative_path`` is what the
                # scanner stored when it indexed the file; it's
                # always safe to join with the bucket root.
                dst = bucket / (record.relative_path or src.name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(src), str(dst))
                    trash_path = dst
                    moved = True
                    log.info(
                        "media.operator_delete.moved_to_trash",
                        media_file_id=record.id,
                        src=str(src),
                        dst=str(dst),
                        actor_id=actor_id,
                    )
                except OSError as exc:
                    # Filesystem-level failure (permission denied,
                    # cross-device link, target exists). Don't
                    # remove the row — the operator should see the
                    # file is still there and retry.
                    log.error(
                        "media.operator_delete.move_failed",
                        media_file_id=record.id,
                        error=str(exc),
                    )
                    raise
            else:
                # File already gone from disk. The row remains
                # ours to remove — that's still useful (an orphan
                # cleanup), but the trash_path stays None so the
                # audit row is honest about it.
                log.warning(
                    "media.operator_delete.source_missing",
                    media_file_id=record.id,
                    path=record.path,
                )

        # Audit BEFORE the row delete so a flush failure still
        # leaves a record of intent. ``actor_label="operator"`` to
        # distinguish from rule-engine deletes.
        await self._audit.record(
            action="file.deleted",
            actor_id=actor_id,
            actor_label="operator",
            target_type="media_file",
            target_id=record.id,
            metadata={
                "path": record.path,
                "reason": reason,
                "remove_from_disk": remove_from_disk,
                "trash_path": str(trash_path) if trash_path else None,
            },
        )

        if self._bus is not None:
            await self._bus.emit(
                MEDIA_DELETED,
                {
                    "id": record.id,
                    "path": record.path,
                    "reason": reason,
                    "remove_from_disk": remove_from_disk,
                },
                source="media.delete_service",
            )

        await self._session.delete(record)
        return DeleteResult(
            media_id=record.id,
            path=record.path,
            removed_from_disk=moved,
            trash_path=str(trash_path) if trash_path else None,
        )


__all__ = ["MediaDeleteService", "DeleteResult"]
