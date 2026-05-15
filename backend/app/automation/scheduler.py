"""Scheduler service.

Runs in two modes:

* **Tick mode** (``tick()``) — called once a minute by the ARQ cron job.
  Finds schedules whose ``next_run_at`` has passed, enqueues a job
  invocation for each, and advances ``next_run_at``.

* **Direct mode** (``run_job_now()``) — invoked by the API "run now"
  button and by the scanner/rule hooks that want to fire ad-hoc work.
  Goes through the same :class:`JobCatalogue` dispatch path so the run
  is recorded identically.

A :class:`JobRun` row is created on every dispatch and updated to its
terminal status by :meth:`execute`. The scheduler is intentionally
agnostic about whether the runner lives in-process or hops through ARQ
— that decision is made by the caller.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.catalogue import JobCatalogue, get_catalogue
from app.automation.cron import next_run
from app.core.logging import get_logger
from app.events.bus import EventBus
from app.models.job_run import JobRun
from app.models.schedule import Schedule
from app.services.repositories import JobRunRepository, ScheduleRepository
from app.utils.datetime import utcnow

log = get_logger("auditarr.automation.scheduler", category="automation")


@dataclass(slots=True)
class TickReport:
    """Summary of one scheduler tick."""

    enqueued: list[str]
    rescheduled: list[str]


class Scheduler:
    def __init__(
        self,
        *,
        session: AsyncSession,
        event_bus: EventBus | None = None,
        catalogue: JobCatalogue | None = None,
    ) -> None:
        self._session = session
        self._bus = event_bus
        self._catalogue = catalogue or get_catalogue()
        self._schedules = ScheduleRepository(session)
        self._runs = JobRunRepository(session)

    # ── Schedule lifecycle ───────────────────────────────────────
    async def prime_next_run(self, schedule: Schedule, *, now: _dt.datetime | None = None) -> None:
        """Compute and persist ``next_run_at`` from the cron spec."""
        when = next_run(schedule.cron, now or utcnow())
        schedule.next_run_at = when

    # ── Tick: find what's due, dispatch it ───────────────────────
    async def tick(
        self,
        ctx: dict[str, Any],
        *,
        now: _dt.datetime | None = None,
    ) -> TickReport:
        now = now or utcnow()
        due = await self._schedules.list_due(now)
        enqueued: list[str] = []
        rescheduled: list[str] = []

        for schedule in due:
            try:
                run = await self._dispatch(
                    schedule=schedule,
                    args=schedule.job_args,
                    trigger="schedule",
                    now=now,
                    ctx=ctx,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "scheduler.dispatch_failed",
                    schedule_id=schedule.id,
                    job_kind=schedule.job_kind,
                    error=str(exc),
                )
                schedule.last_status = "failed"
            else:
                enqueued.append(run.id)
                schedule.last_run_at = now
                schedule.last_status = run.status
            finally:
                # Always advance the cursor so a single bad run doesn't
                # stall a schedule forever.
                try:
                    await self.prime_next_run(schedule, now=now)
                    rescheduled.append(schedule.id)
                except Exception:  # noqa: BLE001
                    log.exception(
                        "scheduler.reschedule_failed",
                        schedule_id=schedule.id,
                    )

        await self._session.commit()
        return TickReport(enqueued=enqueued, rescheduled=rescheduled)

    # ── Manual run ───────────────────────────────────────────────
    async def run_job_now(
        self,
        *,
        job_kind: str,
        args: dict[str, Any],
        ctx: dict[str, Any],
        trigger: str = "manual",
        schedule: Schedule | None = None,
    ) -> JobRun:
        run = await self._dispatch(
            schedule=schedule,
            args=args,
            trigger=trigger,
            job_kind=job_kind,
            now=utcnow(),
            ctx=ctx,
        )
        await self._session.commit()
        return run

    # ── Core dispatch (in-process for now) ───────────────────────
    async def _dispatch(
        self,
        *,
        schedule: Schedule | None,
        args: dict[str, Any],
        trigger: str,
        now: _dt.datetime,
        ctx: dict[str, Any],
        job_kind: str | None = None,
    ) -> JobRun:
        kind = job_kind or (schedule.job_kind if schedule else None)
        if not kind:
            raise ValueError("dispatch needs either a schedule or job_kind")
        self._catalogue.validate_args(kind, args)
        spec = self._catalogue.require(kind)

        run = JobRun(
            schedule_id=schedule.id if schedule else None,
            job_kind=kind,
            job_args=dict(args),
            status="running",
            started_at=now,
            trigger=trigger,
        )
        await self._runs.add(run)
        await self._session.commit()  # surface the run before it executes

        if self._bus is not None:
            await self._bus.emit(
                "job.started",
                {
                    "run_id": run.id,
                    "job_kind": kind,
                    "schedule_id": schedule.id if schedule else None,
                    "trigger": trigger,
                },
                source="automation",
            )

        # Execute inline. The ARQ worker (Stage 4+ infrastructure) can
        # later wrap this in a queue dispatch — the recorded JobRun shape
        # doesn't change.
        try:
            result = await spec.runner(self._session, dict(args), ctx)
            run.status = "completed"
            run.result = result if isinstance(result, dict) else {"value": result}
        except Exception as exc:  # noqa: BLE001
            log.exception("job.runner_failed", job_kind=kind, run_id=run.id)
            run.status = "failed"
            run.error = str(exc)[:2000]
        finally:
            run.finished_at = utcnow()
            run.duration_ms = int(
                (run.finished_at - run.started_at).total_seconds() * 1000
            )
            await self._session.commit()

        if self._bus is not None:
            await self._bus.emit(
                "job.completed" if run.status == "completed" else "job.failed",
                {
                    "run_id": run.id,
                    "job_kind": kind,
                    "schedule_id": schedule.id if schedule else None,
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error": run.error,
                },
                source="automation",
            )

        return run
