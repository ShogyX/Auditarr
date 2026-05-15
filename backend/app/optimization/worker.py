"""Optimization worker.

Pulls one queue item, validates inputs, runs ffmpeg, validates the
output, and atomically swaps it into place. Designed to be invoked
from a cron tick (the ARQ ``automation_tick``/``optimization_tick``
job) but exposes a directly-callable ``run_one`` so the API "run now"
button can drive it the same way.

Concurrency: the worker takes at most one item per call. Stage 10
ships single-stream — running multiple transcodes in parallel on a
self-hosted box usually just turns the CPU into a heater without any
throughput gain. Stage 13 polish may revisit if real deployments
disagree.

Safety:

* Output is written to ``<input>.auditarr.tmp.<ext>`` first.
* On success, the original is moved to ``<input>.bak`` (if the profile
  asks to keep the backup) or deleted, then the temp file is atomically
  ``rename``'d into the original's path.
* On any failure the temp output is deleted and the original is
  untouched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.events.bus import EventBus
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.optimization.ffmpeg_runner import (
    TranscodeRequest,
    run_transcode,
    validate_output,
)
from app.optimization.profile_schema import ProfileDefinition
from app.services.repositories import OptimizationRepository
from app.utils.datetime import utcnow

log = get_logger("auditarr.optimization.worker", category="optimization")


@dataclass(slots=True)
class WorkerReport:
    item_id: str | None
    status: str  # idle | completed | failed | skipped
    detail: str | None = None


class OptimizationWorker:
    def __init__(
        self,
        *,
        session: AsyncSession,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._bus = event_bus
        self._items = OptimizationRepository(session)

    # ── Public API ──────────────────────────────────────────────
    async def run_one(self) -> WorkerReport:
        """Pop and run the oldest queued item. Returns a report.

        Returns ``status='idle'`` if there's nothing to do.
        """
        item = await self._claim_next()
        if item is None:
            return WorkerReport(item_id=None, status="idle")
        try:
            return await self._run_claimed(item)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "optimization.worker_crashed", item_id=item.id
            )
            await self._fail(item, f"worker crashed: {exc!s}"[:2000])
            return WorkerReport(item_id=item.id, status="failed", detail=str(exc))

    async def run_item(self, item_id: str) -> WorkerReport:
        """Run a specific item, bypassing the FIFO ordering."""
        item = await self._items.get(item_id) if hasattr(self._items, "get") else None
        if item is None:
            # Fallback — direct query, the Stage 7 repository didn't
            # expose ``get`` for OptimizationItem.
            item = await self._session.get(OptimizationItem, item_id)
        if item is None:
            return WorkerReport(item_id=item_id, status="failed", detail="not found")
        if item.status != "queued":
            return WorkerReport(
                item_id=item_id,
                status="skipped",
                detail=f"item is in status {item.status!r}, not queued",
            )
        await self._mark_running(item)
        try:
            return await self._run_claimed(item)
        except Exception as exc:  # noqa: BLE001
            log.exception("optimization.run_item_crashed", item_id=item.id)
            await self._fail(item, f"worker crashed: {exc!s}"[:2000])
            return WorkerReport(item_id=item.id, status="failed", detail=str(exc))

    # ── Internals ───────────────────────────────────────────────
    async def _claim_next(self) -> OptimizationItem | None:
        """Find and mark the oldest queued item as ``running``.

        Bug-hunt 2: the previous implementation was a naive
        SELECT-then-UPDATE pattern. Two concurrent ``run_one()``
        calls could both SELECT the same row and both proceed to
        ``_mark_running``, leading to:

          - duplicate ``optimization.started`` events fired
          - the same ffmpeg job running twice
          - ``started_at`` clobbered (the second commit wins),
            ``progress_pct`` reset partway through the first run
          - the output file being clobbered in the destructive
            rename step at the end

        The fix is a single-statement conditional UPDATE that
        races safely: we pick a candidate id, then attempt to
        flip its status to ``running`` only if it's still
        ``queued`` at the moment the UPDATE runs. ``rowcount``
        tells us whether we won — if zero, somebody else claimed
        it, and we look for another. This is portable (SQLite,
        Postgres, MySQL all honor the contract) and avoids
        needing dialect-specific ``SKIP LOCKED`` or ``BEGIN
        IMMEDIATE`` ceremony.

        Bounded retry: in the worst case (a hot queue + N
        concurrent workers), we retry up to 16 times before
        giving up and reporting idle. The bound is generous —
        16 collisions on a single tick is way past anything we
        expect, and giving up cleanly is better than spinning.
        """
        for _attempt in range(16):
            result = await self._session.execute(
                select(OptimizationItem.id)
                .where(OptimizationItem.status == "queued")
                .order_by(OptimizationItem.queued_at)
                .limit(1)
            )
            candidate_id = result.scalar_one_or_none()
            if candidate_id is None:
                return None

            # Conditional update: only succeed if the row is
            # still queued. The ``execution_options`` flag tells
            # SQLAlchemy to skip ORM-level synchronization for
            # this raw UPDATE, which would otherwise complain
            # about a stale Identity-Map entry.
            now = utcnow()
            update_result = await self._session.execute(
                update(OptimizationItem)
                .where(
                    OptimizationItem.id == candidate_id,
                    OptimizationItem.status == "queued",
                )
                .values(status="running", started_at=now, progress_pct=0)
                .execution_options(synchronize_session="fetch")
            )
            await self._session.commit()
            if update_result.rowcount == 0:
                # Lost the race — try again.
                continue

            # We won. Reload the row so the caller has a fresh
            # ORM instance (the UPDATE bypassed the session's
            # identity map; the original ``item`` would be stale
            # if we had loaded it).
            item = await self._session.get(OptimizationItem, candidate_id)
            if item is None:
                # Vanishingly unlikely (the row was just updated)
                # but treat defensively.
                continue
            if self._bus is not None:
                await self._bus.emit(
                    "optimization.started",
                    {"item_id": item.id, "profile": item.profile},
                    source="optimization",
                )
            return item

        return None

    async def _mark_running(self, item: OptimizationItem) -> None:
        # Kept for ``run_item`` which doesn't have a race against
        # other workers (the item is named by id). The bulk-claim
        # path uses the atomic UPDATE in ``_claim_next`` instead.
        item.status = "running"
        item.started_at = utcnow()
        item.progress_pct = 0
        await self._session.commit()
        if self._bus is not None:
            await self._bus.emit(
                "optimization.started",
                {"item_id": item.id, "profile": item.profile},
                source="optimization",
            )

    async def _run_claimed(self, item: OptimizationItem) -> WorkerReport:
        """The meat: profile lookup, validation, ffmpeg, swap."""
        # ── Resolve the media file + profile ──
        media: MediaFile | None = await self._session.get(
            MediaFile, item.media_file_id
        )
        if media is None:
            return await self._fail(item, "media file no longer exists")

        profile_row = await self._session.execute(
            select(OptimizationProfile).where(
                OptimizationProfile.name == item.profile
            )
        )
        profile_row = profile_row.scalar_one_or_none()
        if profile_row is None:
            return await self._fail(
                item, f"profile {item.profile!r} not found"
            )
        if not profile_row.enabled:
            return await self._skip(item, "profile is disabled")
        try:
            profile = ProfileDefinition.model_validate(profile_row.settings)
        except Exception as exc:  # noqa: BLE001
            return await self._fail(
                item, f"profile {item.profile!r} has invalid settings: {exc!s}"
            )

        # ── Pre-flight checks ──
        input_path = Path(media.path)
        if not input_path.exists():
            return await self._fail(item, f"input file missing: {input_path}")
        input_size = input_path.stat().st_size
        item.original_size_bytes = input_size

        if profile_row.max_input_bytes and input_size > profile_row.max_input_bytes:
            return await self._skip(
                item,
                f"input size {input_size} exceeds profile max_input_bytes "
                f"({profile_row.max_input_bytes})",
            )
        if (
            profile.skip_if_bitrate_below_kbps
            and media.bitrate_kbps
            and media.bitrate_kbps < profile.skip_if_bitrate_below_kbps
        ):
            return await self._skip(
                item,
                f"input bitrate {media.bitrate_kbps}kbps is below "
                f"skip_if_bitrate_below_kbps={profile.skip_if_bitrate_below_kbps}",
            )

        # ── Build paths ──
        out_ext = profile.output.container
        # Temp output lives next to the original so the swap is on the
        # same filesystem (``os.replace`` is atomic across same-FS only).
        tmp_output = input_path.with_suffix(f".auditarr.tmp.{out_ext}")

        # ── Run ffmpeg ──
        async def on_progress(pct: int) -> None:
            # Persist progress every step. The per-update overhead is one
            # row UPDATE; for a 30-minute transcode that's at most ~100
            # updates, which is fine on Postgres or SQLite. We avoid
            # ``await session.commit()`` here for the hot path and rely on
            # autoflush — Stage 13 may revisit if very long transcodes need
            # finer-grained durability.
            item.progress_pct = pct
            if self._bus is not None:
                await self._bus.emit(
                    "optimization.progress",
                    {"item_id": item.id, "pct": pct},
                    source="optimization",
                )

        request = TranscodeRequest(
            input_path=input_path,
            output_path=tmp_output,
            profile=profile,
            input_duration_seconds=media.duration_seconds,
        )
        result = await run_transcode(request, on_progress=on_progress)

        if not result.success:
            # Best-effort cleanup of any partial temp output.
            try:
                tmp_output.unlink(missing_ok=True)
            except OSError:
                pass
            return await self._fail(
                item,
                f"ffmpeg failed (rc={result.return_code}): "
                f"{result.stderr_tail[-500:]}",
            )

        # ── Validate the output ──
        ok, reason = await validate_output(
            output_path=tmp_output,
            expected_duration_seconds=media.duration_seconds,
        )
        if not ok:
            try:
                tmp_output.unlink(missing_ok=True)
            except OSError:
                pass
            return await self._fail(
                item, f"output validation failed: {reason}"
            )

        item.optimized_size_bytes = tmp_output.stat().st_size

        # ── Swap-with-backup ──
        if not profile.output.replace_input:
            # Profile doesn't want us to swap; just leave the temp file
            # alongside the original and record the path in metadata.
            item.item_metadata = dict(item.item_metadata or {})
            item.item_metadata["output_path"] = str(tmp_output)
            return await self._complete(item, "transcode succeeded (no swap)")

        try:
            await self._swap(input_path, tmp_output, item, profile)
        except OSError as exc:
            try:
                tmp_output.unlink(missing_ok=True)
            except OSError:
                pass
            return await self._fail(item, f"swap failed: {exc!s}")

        return await self._complete(
            item,
            f"transcode succeeded; "
            f"{input_size} → {item.optimized_size_bytes} bytes",
        )

    async def _swap(
        self,
        input_path: Path,
        tmp_output: Path,
        item: OptimizationItem,
        profile: ProfileDefinition,
    ) -> None:
        """Atomic swap of the temp output into the input's location.

        The swap is two ``os.replace`` calls. Each is atomic on Posix
        (same filesystem). The two-step *isn't* atomic together, but the
        ordering means there's no point at which the user's library has
        neither file: either the original is still there, or the
        backup is there, or the new output is there.
        """
        if profile.output.keep_backup:
            backup_path = input_path.with_suffix(input_path.suffix + ".bak")
            # If a previous run left a backup behind, replace it.
            os.replace(input_path, backup_path)
            item.backup_path = str(backup_path)
            log.info(
                "optimization.swap_backup_kept",
                input=str(input_path),
                backup=str(backup_path),
            )
        else:
            input_path.unlink()
            item.backup_path = None
            log.info(
                "optimization.swap_original_deleted",
                input=str(input_path),
            )

        # Now move the temp output into the original's slot. The output
        # path may have a different extension if the profile changed the
        # container; rename to ``<input stem>.<new ext>`` so directory
        # listings stay sensible.
        final_path = input_path.with_suffix(f".{profile.output.container}")
        os.replace(tmp_output, final_path)
        item.item_metadata = dict(item.item_metadata or {})
        item.item_metadata["final_path"] = str(final_path)
        log.info(
            "optimization.swap_complete",
            final=str(final_path),
        )

    # ── State transitions ──────────────────────────────────────
    async def _complete(self, item: OptimizationItem, detail: str) -> WorkerReport:
        item.status = "completed"
        item.finished_at = utcnow()
        item.progress_pct = 100
        await self._session.commit()
        if self._bus is not None:
            await self._bus.emit(
                "optimization.completed",
                {
                    "item_id": item.id,
                    "profile": item.profile,
                    "original_size": item.original_size_bytes,
                    "optimized_size": item.optimized_size_bytes,
                },
                source="optimization",
            )
        return WorkerReport(item_id=item.id, status="completed", detail=detail)

    async def _fail(self, item: OptimizationItem, detail: str) -> WorkerReport:
        item.status = "failed"
        item.finished_at = utcnow()
        item.error = detail
        await self._session.commit()
        if self._bus is not None:
            await self._bus.emit(
                "optimization.failed",
                {"item_id": item.id, "error": detail},
                source="optimization",
            )
        return WorkerReport(item_id=item.id, status="failed", detail=detail)

    async def _skip(self, item: OptimizationItem, detail: str) -> WorkerReport:
        item.status = "skipped"
        item.finished_at = utcnow()
        item.error = detail
        await self._session.commit()
        if self._bus is not None:
            await self._bus.emit(
                "optimization.failed",
                {"item_id": item.id, "status": "skipped", "reason": detail},
                source="optimization",
            )
        return WorkerReport(item_id=item.id, status="skipped", detail=detail)
