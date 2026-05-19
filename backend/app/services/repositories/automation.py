"""Repositories for the automation subsystem."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_run import JobRun
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.models.schedule import Schedule


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, schedule: Schedule) -> Schedule:
        self._session.add(schedule)
        await self._session.flush()
        return schedule

    async def get(self, schedule_id: str) -> Schedule | None:
        return await self._session.get(Schedule, schedule_id)

    async def get_by_name(self, name: str) -> Schedule | None:
        result = await self._session.execute(
            select(Schedule).where(Schedule.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self, *, enabled_only: bool = False
    ) -> Sequence[Schedule]:
        stmt = select(Schedule).order_by(Schedule.name)
        if enabled_only:
            stmt = stmt.where(Schedule.enabled.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_due(self, now: _dt.datetime) -> Sequence[Schedule]:
        """Schedules whose ``next_run_at`` has passed."""
        result = await self._session.execute(
            select(Schedule).where(
                Schedule.enabled.is_(True), Schedule.next_run_at <= now
            )
        )
        return result.scalars().all()

    async def delete(self, schedule: Schedule) -> None:
        await self._session.delete(schedule)
        await self._session.flush()


class JobRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: JobRun) -> JobRun:
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: str) -> JobRun | None:
        return await self._session.get(JobRun, run_id)

    async def list_recent(
        self,
        *,
        schedule_id: str | None = None,
        job_kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> Sequence[JobRun]:
        stmt = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
        if schedule_id:
            stmt = stmt.where(JobRun.schedule_id == schedule_id)
        if job_kind:
            stmt = stmt.where(JobRun.job_kind == job_kind)
        if status:
            stmt = stmt.where(JobRun.status == status)
        result = await self._session.execute(stmt)
        return result.scalars().all()


class OptimizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, item_id: str) -> OptimizationItem | None:
        """Stage 10 needs by-id lookup for the manual run/cancel endpoints."""
        return await self._session.get(OptimizationItem, item_id)

    async def upsert_queued(
        self,
        *,
        media_file_id: str,
        profile: str,
        rule_id: str | None,
        queued_at: _dt.datetime,
    ) -> OptimizationItem:
        """Insert or refresh a queue entry keyed by (file, profile).

        We only upsert *queued* state. If the entry is already
        ``running``/``completed``/``failed`` we leave it alone — Stage 10
        will own those transitions and shouldn't have rule re-evaluations
        race-resetting them.
        """
        result = await self._session.execute(
            select(OptimizationItem).where(
                OptimizationItem.media_file_id == media_file_id,
                OptimizationItem.profile == profile,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            item = OptimizationItem(
                media_file_id=media_file_id,
                profile=profile,
                status="queued",
                queued_by_rule_id=rule_id,
                queued_at=queued_at,
                item_metadata={},
            )
            self._session.add(item)
            await self._session.flush()
            return item
        if existing.status == "queued":
            # Re-queueing while already queued is a no-op but refresh the
            # rule attribution + queued_at so we know which rule re-matched.
            existing.queued_by_rule_id = rule_id
            existing.queued_at = queued_at
            await self._session.flush()
        return existing

    async def list_pending(self, limit: int = 100) -> Sequence[OptimizationItem]:
        result = await self._session.execute(
            select(OptimizationItem)
            .where(OptimizationItem.status == "queued")
            .order_by(OptimizationItem.queued_at)
            .limit(limit)
        )
        return result.scalars().all()

    async def list_all(
        self, *, status: str | None = None, limit: int = 100
    ) -> Sequence[OptimizationItem]:
        stmt = (
            select(OptimizationItem)
            .order_by(OptimizationItem.queued_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(OptimizationItem.status == status)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_active_for_profile(self, profile_name: str) -> int:
        """Number of non-terminal items referencing ``profile_name``.

        Active = ``queued``, ``running``, or ``routed``. Deleting a
        profile that still has active items leaves them looping with
        the worker raising "profile X not found" forever, so the
        delete endpoint uses this to 409 instead.
        """
        from sqlalchemy import func

        result = await self._session.execute(
            select(func.count(OptimizationItem.id)).where(
                OptimizationItem.profile == profile_name,
                OptimizationItem.status.in_(("queued", "running", "routed")),
            )
        )
        return int(result.scalar_one() or 0)


class OptimizationProfileRepository:
    """Profile CRUD; new in Stage 10."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, profile: OptimizationProfile) -> OptimizationProfile:
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def get(self, profile_id: str) -> OptimizationProfile | None:
        return await self._session.get(OptimizationProfile, profile_id)

    async def get_by_name(self, name: str) -> OptimizationProfile | None:
        result = await self._session.execute(
            select(OptimizationProfile).where(
                OptimizationProfile.name == name
            )
        )
        return result.scalar_one_or_none()

    async def list_all(
        self, *, enabled_only: bool = False
    ) -> Sequence[OptimizationProfile]:
        stmt = select(OptimizationProfile).order_by(OptimizationProfile.name)
        if enabled_only:
            stmt = stmt.where(OptimizationProfile.enabled.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete(self, profile: OptimizationProfile) -> None:
        await self._session.delete(profile)
        await self._session.flush()
