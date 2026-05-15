"""ScanRun repository."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan_run import ScanRun


class ScanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: ScanRun) -> ScanRun:
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: str) -> ScanRun | None:
        return await self._session.get(ScanRun, run_id)

    async def list_for_library(
        self, library_id: str, *, limit: int = 20
    ) -> Sequence[ScanRun]:
        result = await self._session.execute(
            select(ScanRun)
            .where(ScanRun.library_id == library_id)
            .order_by(ScanRun.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_recent(self, limit: int = 20) -> Sequence[ScanRun]:
        result = await self._session.execute(
            select(ScanRun).order_by(ScanRun.created_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def find_active_for_library(self, library_id: str) -> ScanRun | None:
        """Return the in-flight (queued or running) scan for a library,
        or ``None`` if no scan is currently active.

        Bug-hunt 2: ``trigger_scan`` uses this to refuse a concurrent
        scan of the same library. ``queued`` counts as active because
        we've already promised to run that scan; starting a second
        one would race the worker that picks up the queued row.
        """
        result = await self._session.execute(
            select(ScanRun)
            .where(
                ScanRun.library_id == library_id,
                ScanRun.status.in_(["queued", "running"]),
            )
            .order_by(ScanRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
