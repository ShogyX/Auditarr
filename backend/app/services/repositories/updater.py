"""Update check + apply repositories."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.update_apply import UpdateApply
from app.models.update_check import UpdateCheck
from app.utils.datetime import utcnow


class UpdateCheckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: UpdateCheck) -> UpdateCheck:
        self._session.add(row)
        await self._session.flush()
        return row

    async def latest(self) -> UpdateCheck | None:
        """The most recent check row, regardless of ok/not."""
        result = await self._session.execute(
            select(UpdateCheck)
            .order_by(UpdateCheck.checked_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 20) -> Sequence[UpdateCheck]:
        result = await self._session.execute(
            select(UpdateCheck)
            .order_by(UpdateCheck.checked_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


class UpdateApplyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: UpdateApply) -> UpdateApply:
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, apply_id: str) -> UpdateApply | None:
        return await self._session.get(UpdateApply, apply_id)

    async def reap_stale(self, *, timeout_seconds: int) -> int:
        """Force-mark every ``requested``/``running`` row older than
        ``timeout_seconds`` as ``failed``.

        Returns the number of rows transitioned. Idempotent — re-running
        with the same cutoff is a no-op because the next pass finds no
        open rows past the threshold.

        This is the v1.9 Stage 1.2 reaper: the host helper writes
        status transitions back via the status file, but a helper
        that crashes (or was never installed) leaves the row open
        forever, which then blocks ``request_apply`` permanently
        via ``has_open()``. The reaper is the operator-invisible
        escape hatch.
        """
        cutoff = utcnow() - _dt.timedelta(seconds=timeout_seconds)
        # Pull the candidate rows first so we know how many we touched
        # (returning row count from ``execute`` is dialect-dependent
        # for ``UPDATE … WHERE``; the two-step SELECT then UPDATE is
        # portable across sqlite and postgres).
        stale_q = await self._session.execute(
            select(UpdateApply).where(
                UpdateApply.status.in_(["requested", "running"]),
                UpdateApply.started_at < cutoff,
            )
        )
        stale_rows = list(stale_q.scalars().all())
        if not stale_rows:
            return 0
        now = utcnow()
        reaper_msg = (
            "reaper: stale apply, host helper never reported back"
        )
        for row in stale_rows:
            row.status = "failed"
            row.finished_at = now
            row.error = reaper_msg
        await self._session.flush()
        return len(stale_rows)

    async def has_open(self, *, timeout_seconds: int | None = None) -> bool:
        """True if there's a requested or running apply.

        When ``timeout_seconds`` is supplied, rows older than that are
        first force-marked ``failed`` by :meth:`reap_stale`. Callers
        that want the historical "no staleness check" behavior pass
        ``None`` (or omit the kwarg).
        """
        if timeout_seconds is not None:
            await self.reap_stale(timeout_seconds=timeout_seconds)
        result = await self._session.execute(
            select(UpdateApply.id)
            .where(UpdateApply.status.in_(["requested", "running"]))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def force_clear(self, apply_id: str) -> UpdateApply:
        """Manually flip a stuck row to ``failed``.

        Raises ``ValueError`` if the row doesn't exist or isn't in an
        open state (``requested``/``running``). Idempotency: re-running
        on an already-failed row is a no-op error rather than a silent
        success — the operator should know they're shooting at an
        already-dead row.
        """
        row = await self.get(apply_id)
        if row is None:
            raise ValueError(f"Unknown apply {apply_id!r}")
        if row.status not in {"requested", "running"}:
            raise ValueError(
                f"Cannot force-clear apply in status {row.status!r}; "
                f"only requested/running rows can be force-cleared"
            )
        row.status = "failed"
        row.finished_at = utcnow()
        row.error = "force-cleared by operator"
        await self._session.flush()
        return row

    async def list_recent(self, limit: int = 20) -> Sequence[UpdateApply]:
        result = await self._session.execute(
            select(UpdateApply)
            .order_by(UpdateApply.started_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
