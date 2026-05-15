"""Housekeeping (Stage 13).

Daily cron tick that trims old rows from tables that accumulate audit
detail forever otherwise:

* ``notification_deliveries`` — every fired (or skipped) alert.
* ``update_checks``           — every feed poll.
* ``rule_evaluations``        — every per-file rule match (only trimmed
  if explicitly enabled; usually you want to keep these around so the
  Files page can show "this rule fired N days ago").
* ``job_runs``                — every automation job execution.

Retention is per-table, configured via ``AUDITARR_HOUSEKEEPING_*``
settings. A retention of 0 disables that trim — handy for dev and for
operators who genuinely want everything kept.

The deletes use ``DELETE WHERE ... < cutoff`` directly. On Postgres
that's fast; on SQLite the dialect lacks a row-limited delete but the
volumes here are tiny enough that it doesn't matter.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import Settings
from app.models.housekeeping_run import HousekeepingRun
from app.models.job_run import JobRun
from app.models.notification_delivery import NotificationDelivery
from app.models.rule_evaluation import RuleEvaluation
from app.models.update_check import UpdateCheck
from app.utils.datetime import utcnow

log = get_logger("auditarr.housekeeping", category="housekeeping")


@dataclass(slots=True)
class HousekeepingReport:
    """What ``run`` deleted."""

    notification_deliveries: int = 0
    update_checks: int = 0
    rule_evaluations: int = 0
    job_runs: int = 0

    @property
    def total(self) -> int:
        return (
            self.notification_deliveries
            + self.update_checks
            + self.rule_evaluations
            + self.job_runs
        )


class HousekeepingService:
    """Trim old audit rows per the configured retention windows."""

    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def run(self, *, trigger: str = "scheduled") -> HousekeepingReport:
        """Execute the trim. ``trigger`` is recorded on the history
        row so operators can distinguish admin-initiated runs from
        scheduled ones.
        """
        report = HousekeepingReport()
        now = utcnow()
        started_at = now
        error: str | None = None

        try:
            deliveries_days = self._settings.housekeeping_delivery_retention_days
            if deliveries_days > 0:
                cutoff = now - _dt.timedelta(days=deliveries_days)
                result = await self._session.execute(
                    delete(NotificationDelivery).where(
                        NotificationDelivery.attempted_at < cutoff
                    )
                )
                report.notification_deliveries = result.rowcount or 0

            checks_days = self._settings.housekeeping_update_check_retention_days
            if checks_days > 0:
                cutoff = now - _dt.timedelta(days=checks_days)
                result = await self._session.execute(
                    delete(UpdateCheck).where(UpdateCheck.checked_at < cutoff)
                )
                report.update_checks = result.rowcount or 0

            rules_days = self._settings.housekeeping_rule_evaluation_retention_days
            if rules_days > 0:
                cutoff = now - _dt.timedelta(days=rules_days)
                result = await self._session.execute(
                    delete(RuleEvaluation).where(
                        RuleEvaluation.evaluated_at < cutoff
                    )
                )
                report.rule_evaluations = result.rowcount or 0

            jobs_days = self._settings.housekeeping_job_run_retention_days
            if jobs_days > 0:
                cutoff = now - _dt.timedelta(days=jobs_days)
                result = await self._session.execute(
                    delete(JobRun).where(JobRun.started_at < cutoff)
                )
                report.job_runs = result.rowcount or 0
        except Exception as exc:  # noqa: BLE001
            # Stage 14 (audit follow-up): record the failure on the
            # run row so the Settings page surfaces it. Re-raise so
            # the caller sees the exception too — the row is purely
            # an audit-trail artifact.
            error = str(exc)[:1024]
            await self._session.rollback()
            await self._record_run(
                trigger=trigger,
                started_at=started_at,
                finished_at=utcnow(),
                report=report,
                error=error,
            )
            raise

        # Stage 14 (audit follow-up): persist a history row so the
        # operator can see "Last run: <ts> — deleted N rows". Done
        # in the same transaction as the deletes so the audit trail
        # can't lie about what was removed.
        await self._record_run(
            trigger=trigger,
            started_at=started_at,
            finished_at=utcnow(),
            report=report,
            error=None,
        )
        await self._session.commit()
        log.info(
            "housekeeping.complete",
            trigger=trigger,
            deliveries=report.notification_deliveries,
            update_checks=report.update_checks,
            rule_evaluations=report.rule_evaluations,
            job_runs=report.job_runs,
        )
        return report

    async def _record_run(
        self,
        *,
        trigger: str,
        started_at: _dt.datetime,
        finished_at: _dt.datetime,
        report: HousekeepingReport,
        error: str | None,
    ) -> None:
        """Insert one row into ``housekeeping_runs``. Committed by
        the caller. If a failure rolled back the session, the caller
        commits this row on a fresh transaction."""
        self._session.add(
            HousekeepingRun(
                trigger=trigger,
                started_at=started_at,
                finished_at=finished_at,
                deliveries_deleted=report.notification_deliveries,
                update_checks_deleted=report.update_checks,
                rule_evaluations_deleted=report.rule_evaluations,
                job_runs_deleted=report.job_runs,
                error=error,
            )
        )
        if error is not None:
            # On the failure path we need an independent commit so
            # the row survives — the caller already rolled back the
            # session, so this add starts a fresh transaction.
            await self._session.commit()


__all__ = ["HousekeepingReport", "HousekeepingService"]
