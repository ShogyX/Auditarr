"""Stage 08 (v1.7) — routed transcode poller.

Plan §444: when a profile's ``routing_target`` is non-in_process,
the worker submits the job to the integration provider and marks
the item ``routed``. The provider executes asynchronously; the
poller in this module ticks every 5 minutes (via the automation
scheduler) and polls each routed item's provider for completion.

The poll loop:

1. Find every ``OptimizationItem`` in ``routed`` status with a
   stamped ``upstream_job_id`` and ``integration_id``.
2. Resolve the integration → provider → ``IntegrationConfig``.
3. Call ``get_transcode_job_status(config, upstream_job_id)``.
4. Map the returned ``TranscodeJobStatus.status`` to a terminal
   item state:
     * ``"completed"`` → item.status="completed"; emit
       ``optimization.routed_completed``.
     * ``"failed"``    → item.status="failed"; emit
       ``optimization.routed_failed``.
     * ``"running"`` / ``"pending"`` / ``"unknown"`` → leave the
       item where it is and try again on the next tick.

Provider crashes are caught per-item so one bad provider can't
poison the whole poll batch. Each item gets its own try/except.

The poller is intentionally a free function rather than a method
on :class:`OptimizationWorker` because it has different
preconditions (an IntegrationManager is required; without one
nothing can be polled) and a different cadence (every 5 minutes,
independent of the per-tick ffmpeg run). Plan §444 explicitly
names it as a separate automation job.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.events.bus import EventBus
from app.integrations.manager import IntegrationManager
from app.models.integration import Integration
from app.models.optimization import OptimizationItem
from app.utils.datetime import utcnow

log = get_logger("auditarr.optimization.poller", category="optimization")


@dataclass(slots=True)
class PollReport:
    """Summary of one polling pass.

    ``checked`` is the count of items polled this tick.
    ``completed`` / ``failed`` are the counts that reached a
    terminal state. ``still_running`` is the count that polled
    successfully but haven't finished yet (we'll re-check next
    tick). ``errored`` is the count where the poll itself
    failed — those items stay in ``routed`` and the next tick
    re-tries.
    """

    checked: int = 0
    completed: int = 0
    failed: int = 0
    still_running: int = 0
    errored: int = 0


async def poll_routed_transcodes(
    *,
    session: AsyncSession,
    integration_manager: IntegrationManager,
    event_bus: EventBus | None = None,
) -> PollReport:
    """Run one poll pass over every ``routed`` optimization item.

    Plan §444. Designed to be invoked from the automation
    scheduler every 5 minutes. Safe to call concurrently with
    the per-tick ``OptimizationWorker.run_one`` — they operate
    on disjoint item-status sets (``queued`` vs ``routed``).
    """
    report = PollReport()

    result = await session.execute(
        select(OptimizationItem).where(
            OptimizationItem.status == "routed"
        )
    )
    routed_items = list(result.scalars())

    for item in routed_items:
        report.checked += 1
        try:
            await _poll_one(
                item,
                session=session,
                integration_manager=integration_manager,
                event_bus=event_bus,
                report=report,
            )
        except Exception as exc:  # noqa: BLE001
            # Per-item isolation: one bad provider can't poison
            # the rest of the batch. The item stays in ``routed``
            # so the next tick re-tries.
            log.exception(
                "optimization.poll_one_crashed",
                item_id=item.id,
                error=str(exc),
            )
            report.errored += 1

    log.info(
        "optimization.poll_pass",
        checked=report.checked,
        completed=report.completed,
        failed=report.failed,
        still_running=report.still_running,
        errored=report.errored,
    )
    return report


async def _poll_one(
    item: OptimizationItem,
    *,
    session: AsyncSession,
    integration_manager: IntegrationManager,
    event_bus: EventBus | None,
    report: PollReport,
) -> None:
    """Poll a single routed item. Mutates ``report`` counters."""
    metadata = dict(item.item_metadata or {})
    upstream_job_id = metadata.get("upstream_job_id")
    integration_id = metadata.get("integration_id")
    if not upstream_job_id or not integration_id:
        # Routed items missing correlation metadata are leftovers
        # from the Stage 07 seam (worker without manager). They
        # can't be polled; surface and skip.
        log.warning(
            "optimization.poll_skip_missing_metadata",
            item_id=item.id,
        )
        report.errored += 1
        return

    integration = await session.get(Integration, integration_id)
    if integration is None:
        await _mark_failed(
            item,
            (
                f"polled integration_id={integration_id!r} no longer "
                "exists; treating as failed"
            ),
            session=session,
            event_bus=event_bus,
        )
        report.failed += 1
        return

    provider = integration_manager.provider_for(integration.kind)
    if provider is None or not hasattr(provider, "get_transcode_job_status"):
        # No provider → can't determine state. Leave routed.
        # Logged so operators see the issue.
        log.warning(
            "optimization.poll_no_provider_support",
            item_id=item.id,
            integration_kind=integration.kind,
        )
        report.errored += 1
        return

    config = integration_manager.build_config(integration)
    status = await provider.get_transcode_job_status(
        config, str(upstream_job_id)
    )

    # ── Map TranscodeJobStatus → item state ─────────────────────
    if status.status == "completed":
        await _mark_completed(
            item,
            detail=status.detail or "provider reports completed",
            session=session,
            event_bus=event_bus,
        )
        report.completed += 1
    elif status.status == "failed":
        await _mark_failed(
            item,
            status.detail or "provider reports failed",
            session=session,
            event_bus=event_bus,
        )
        report.failed += 1
    elif status.status in ("running", "pending"):
        # Still in flight; update progress if available and leave
        # the item in ``routed``.
        changed = False
        if status.progress_pct is not None:
            new_pct = max(0, min(100, int(status.progress_pct)))
            if item.progress_pct != new_pct:
                item.progress_pct = new_pct
                changed = True
        if changed:
            await session.commit()
        report.still_running += 1
    else:
        # ``unknown`` or any other value — leave routed and re-
        # poll next tick.
        report.still_running += 1


async def _mark_completed(
    item: OptimizationItem,
    *,
    detail: str,
    session: AsyncSession,
    event_bus: EventBus | None,
) -> None:
    item.status = "completed"
    item.finished_at = utcnow()
    item.progress_pct = 100
    metadata = dict(item.item_metadata or {})
    metadata["completed_at"] = item.finished_at.isoformat()
    metadata["completed_detail"] = detail
    item.item_metadata = metadata
    await session.commit()
    if event_bus is not None:
        await event_bus.emit(
            "optimization.routed_completed",
            {
                "item_id": item.id,
                "profile": item.profile,
                "upstream_job_id": metadata.get("upstream_job_id"),
                "integration_id": metadata.get("integration_id"),
                "detail": detail,
            },
            source="optimization",
        )
    log.info(
        "optimization.routed_completed",
        item_id=item.id,
        profile=item.profile,
        upstream_job_id=metadata.get("upstream_job_id"),
    )


async def _mark_failed(
    item: OptimizationItem,
    detail: str,
    *,
    session: AsyncSession,
    event_bus: EventBus | None,
) -> None:
    item.status = "failed"
    item.finished_at = utcnow()
    item.error = detail[:2000]
    metadata = dict(item.item_metadata or {})
    metadata["failed_at"] = item.finished_at.isoformat()
    metadata["failed_detail"] = detail
    item.item_metadata = metadata
    await session.commit()
    if event_bus is not None:
        await event_bus.emit(
            "optimization.routed_failed",
            {
                "item_id": item.id,
                "profile": item.profile,
                "upstream_job_id": metadata.get("upstream_job_id"),
                "integration_id": metadata.get("integration_id"),
                "error": detail,
            },
            source="optimization",
        )
    log.warning(
        "optimization.routed_failed",
        item_id=item.id,
        profile=item.profile,
        error=detail,
    )
