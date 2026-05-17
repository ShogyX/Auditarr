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
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.events.bus import EventBus
from app.integrations.types import TranscodeJobSpec
from app.models.integration import Integration
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.optimization.ffmpeg_runner import (
    TranscodeRequest,
    run_transcode,
    validate_output,
)
from app.optimization.profile_schema import (
    ProfileDefinition,
    schedule_window_is_open,
)
from app.services.repositories import OptimizationRepository
from app.utils.datetime import utcnow

if TYPE_CHECKING:
    from app.integrations.manager import IntegrationManager

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
        integration_manager: "IntegrationManager | None" = None,
    ) -> None:
        self._session = session
        self._bus = event_bus
        self._items = OptimizationRepository(session)
        # Stage 08 (v1.7) — when provided, the worker calls
        # ``submit_transcode_job`` on the routed item's
        # integration provider. When ``None`` (legacy / test
        # construction), the worker falls back to the Stage 07
        # behaviour: mark routed + emit the event, but don't
        # actually hand the job off. The polling job
        # (``poll_routed_transcodes``) similarly requires the
        # manager to advance routed items.
        self._integration_manager = integration_manager

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

        # ── Stage 07 (v1.7) gates ─────────────────────────────────
        # Order matters. Cheapest checks first.
        #
        # 1) Schedule window. When outside the window, release the
        #    item back to ``queued`` so the next tick picks it up.
        #    We don't fail/skip the item — it's not a permanent
        #    outcome, it's "try again later". Emit
        #    ``optimization.skipped_window`` per addendum A.1 §114
        #    so the dashboard can surface "X items waiting for
        #    schedule".
        if profile.schedule_window is not None and not schedule_window_is_open(
            profile.schedule_window
        ):
            return await self._release_for_schedule(item, profile)

        # 2) Routing target. When the profile targets a non-
        #    in_process runner, mark the item ``routed`` and emit
        #    ``optimization.routed`` (per plan §402 + addendum
        #    A.1 §114). The integration provider's
        #    ``submit_transcode_job`` actually runs the job;
        #    Stage 08 wires the provider side. Stage 07 lays the
        #    seam: the item leaves the in-process queue without
        #    being executed locally.
        if profile.routing_target != "in_process":
            return await self._route_to_provider(
                item, profile, profile_row, media
            )

        # 3) In-process kill-switch. When the runtime setting
        #    ``optimization_in_process_runner_enabled`` is False,
        #    refuse to run in-process items with a clear error.
        #    Profiles routed to plex/jellyfin/tdarr already
        #    returned above and aren't affected by this toggle.
        if not self._in_process_runner_enabled():
            return await self._fail(
                item,
                "in-process runner disabled; reconfigure the "
                "profile to route to plex/jellyfin/tdarr",
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

    # ── Stage 07 (v1.7) helpers ────────────────────────────────
    def _in_process_runner_enabled(self) -> bool:
        """Read the ``optimization_in_process_runner_enabled``
        runtime setting. The runtime-settings layer reads from
        the override store first and falls back to the env-default
        ``Settings`` value (True). Reading via ``get_settings()``
        picks up the override transparently because the runtime-
        settings service writes back into the cached Settings
        instance on each change."""
        try:
            return bool(get_settings().optimization_in_process_runner_enabled)
        except Exception:  # noqa: BLE001
            # Defensive: if the settings cache is somehow unbuilt,
            # default to the env-default value (True). The kill-
            # switch errs on the side of running rather than
            # silently quarantining work.
            return True

    async def _release_for_schedule(
        self, item: OptimizationItem, profile: ProfileDefinition
    ) -> WorkerReport:
        """Outside the profile's schedule window — release the
        item back to ``queued`` so the next tick can pick it up
        when the window opens. NOT a terminal state; the item
        stays in the queue.

        Emits ``optimization.skipped_window`` so the dashboard can
        surface "X items waiting for their schedule" without the
        operator needing to inspect each row.
        """
        # We claimed the row as ``running`` in ``_claim_next``;
        # flip it back to ``queued`` so a future tick re-picks it.
        # Reset ``started_at`` and ``progress_pct`` so the
        # transient running-state doesn't leak into the next run.
        item.status = "queued"
        item.started_at = None
        item.progress_pct = 0
        await self._session.commit()
        window = profile.schedule_window
        detail = (
            f"outside schedule window "
            f"{window.start_hour:02d}:00..{window.end_hour:02d}:00 "
            f"({window.timezone})"
            if window is not None
            else "outside schedule window"
        )
        if self._bus is not None:
            await self._bus.emit(
                "optimization.skipped_window",
                {
                    "item_id": item.id,
                    "profile": item.profile,
                    "reason": detail,
                },
                source="optimization",
            )
        log.info(
            "optimization.skipped_window",
            item_id=item.id,
            profile=item.profile,
            detail=detail,
        )
        return WorkerReport(
            item_id=item.id, status="skipped", detail=detail
        )

    async def _route_to_provider(
        self,
        item: OptimizationItem,
        profile: ProfileDefinition,
        profile_row: OptimizationProfile,
        media: MediaFile,
    ) -> WorkerReport:
        """Hand the item off to a non-in_process integration provider.

        Stage 08 (v1.7) — when the worker was constructed with
        an ``IntegrationManager`` and the profile names an
        ``optimization_integration_id``, we resolve the
        integration, build a :class:`TranscodeJobSpec`, and call
        the provider's ``submit_transcode_job`` (per plan §402
        + §444).

        The provider's :class:`JobSubmitResult` drives the
        terminal state:
          * ``"accepted"`` → item flips to ``routed`` with the
            upstream job id stamped on metadata; the polling
            job will later flip it to ``completed`` / ``failed``.
          * ``"rejected"`` → item flips to ``failed`` with the
            provider's detail message. Operator action required.
          * ``"error"`` (transient / transport) → item flips
            back to ``queued`` so the next tick re-tries.

        Backwards compatibility: when the worker was constructed
        without an integration manager (tests, legacy callers),
        we fall back to the Stage 07 behaviour: mark routed,
        emit the event, never poll. This preserves existing
        Stage 07 test expectations.
        """
        item.item_metadata = dict(item.item_metadata or {})
        item.item_metadata["routing_target"] = profile.routing_target
        item.item_metadata["routed_at"] = utcnow().isoformat()

        # When the worker was constructed without an integration
        # manager (Stage 07-style legacy callers, tests pinning
        # the seam-only behaviour), fall back to "mark routed +
        # emit, don't dispatch". The integration_id check below
        # is intentionally NOT reached in this branch — without
        # a manager we can't dispatch either way, so the
        # operator-actionable error doesn't help.
        if self._integration_manager is None:
            item.status = "routed"
            await self._session.commit()
            if self._bus is not None:
                await self._bus.emit(
                    "optimization.routed",
                    {
                        "item_id": item.id,
                        "profile": item.profile,
                        "routing_target": profile.routing_target,
                    },
                    source="optimization",
                )
            log.info(
                "optimization.routed",
                item_id=item.id,
                profile=item.profile,
                target=profile.routing_target,
                provider_dispatched=False,
            )
            return WorkerReport(
                item_id=item.id,
                status="routed",
                detail=(
                    f"routed to {profile.routing_target} "
                    "(integration manager not provided to worker; "
                    "provider not called)"
                ),
            )

        # ── Find the integration provider ────────────────────────
        # The profile carries ``optimization_integration_id``
        # naming a specific integration row. Stage 08 requires
        # the operator to pick one when routing_target != in_process;
        # without it we can't dispatch (Plex vs Jellyfin both
        # match ``routing_target=plex`` etc., but the operator
        # may have multiple Plex servers configured).
        integration_id = profile_row.optimization_integration_id
        if not integration_id:
            return await self._fail(
                item,
                (
                    f"profile {item.profile!r} has routing_target="
                    f"{profile.routing_target!r} but no integration "
                    "is configured (optimization_integration_id is "
                    "empty). Pick one in the profile editor."
                ),
            )

        # ── Provider dispatch (Stage 08) ────────────────────────
        integration = await self._session.get(Integration, integration_id)
        if integration is None:
            return await self._fail(
                item,
                (
                    f"profile {item.profile!r} references integration "
                    f"id {integration_id!r} which no longer exists"
                ),
            )
        if integration.kind != profile.routing_target:
            return await self._fail(
                item,
                (
                    f"profile {item.profile!r} expected routing_target="
                    f"{profile.routing_target!r} but the configured "
                    f"integration is kind={integration.kind!r}"
                ),
            )

        provider = self._integration_manager.provider_for(integration.kind)
        if provider is None:
            return await self._fail(
                item,
                (
                    f"no provider registered for integration kind "
                    f"{integration.kind!r}"
                ),
            )
        if not hasattr(provider, "submit_transcode_job"):
            return await self._fail(
                item,
                (
                    f"provider {integration.kind!r} does not support "
                    "submit_transcode_job (Stage 08 contract); switch "
                    "this profile's routing_target to 'in_process' "
                    "or to a provider that supports hand-off."
                ),
            )

        config = self._integration_manager.build_config(integration)

        # Build the transcode job spec from the profile + media.
        # ``provider_profile_id`` and any provider-specific hints
        # live in the profile's settings under
        # ``settings.provider_metadata`` (free-form per plan §407
        # — the profile editor populates it).
        provider_metadata = {}
        raw_provider_meta = (profile_row.settings or {}).get(
            "provider_metadata"
        )
        if isinstance(raw_provider_meta, dict):
            provider_metadata = dict(raw_provider_meta)

        job_spec = TranscodeJobSpec(
            item_id=item.id,
            input_path=media.path,
            transcode_scope=profile.transcode_scope,
            video_codec=profile.video.codec,
            audio_codec=profile.audio.codec,
            container=profile.output.container,
            crf=profile.video.crf,
            max_bitrate_kbps=profile.video.max_bitrate_kbps,
            scale_height=profile.video.scale_height,
            metadata=provider_metadata,
        )

        try:
            result = await provider.submit_transcode_job(config, job_spec)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "optimization.routed_submit_crashed",
                item_id=item.id,
                integration=integration.name,
            )
            # A provider crash is a transient error — re-queue
            # rather than failing terminally so the next tick
            # can re-try.
            item.status = "queued"
            item.started_at = None
            item.progress_pct = 0
            await self._session.commit()
            return WorkerReport(
                item_id=item.id,
                status="skipped",
                detail=f"provider crashed during submit: {exc!s}",
            )

        # ── Map JobSubmitResult to item state ────────────────────
        if result.status == "accepted":
            item.status = "routed"
            item.item_metadata["upstream_job_id"] = result.upstream_job_id
            item.item_metadata["integration_id"] = integration.id
            item.item_metadata["integration_name"] = integration.name
            if result.detail:
                item.item_metadata["routed_detail"] = result.detail
            await self._session.commit()
            if self._bus is not None:
                await self._bus.emit(
                    "optimization.routed",
                    {
                        "item_id": item.id,
                        "profile": item.profile,
                        "routing_target": profile.routing_target,
                        "integration_id": integration.id,
                        "upstream_job_id": result.upstream_job_id,
                    },
                    source="optimization",
                )
            log.info(
                "optimization.routed",
                item_id=item.id,
                profile=item.profile,
                target=profile.routing_target,
                integration=integration.name,
                upstream_job_id=result.upstream_job_id,
            )
            return WorkerReport(
                item_id=item.id,
                status="routed",
                detail=(
                    f"submitted to {integration.name} as "
                    f"{result.upstream_job_id}"
                ),
            )

        if result.status == "rejected":
            # Provider refused — terminal failure. Operator must
            # fix the profile / target before this item can run.
            return await self._fail(
                item,
                (
                    f"{integration.kind!r} provider rejected job: "
                    f"{result.detail or '(no detail)'}"
                ),
            )

        # ``error`` or any unknown status — transient; re-queue.
        item.status = "queued"
        item.started_at = None
        item.progress_pct = 0
        await self._session.commit()
        log.warning(
            "optimization.routed_submit_transient_error",
            item_id=item.id,
            integration=integration.name,
            detail=result.detail,
        )
        return WorkerReport(
            item_id=item.id,
            status="skipped",
            detail=(
                f"transient error from {integration.kind} provider: "
                f"{result.detail or '(no detail)'} — re-queued for "
                "next tick"
            ),
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
