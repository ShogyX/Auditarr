"""Refresh-session repository."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import RefreshSession


class RefreshSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, jti: str) -> RefreshSession | None:
        return await self._session.get(RefreshSession, jti)

    async def add(self, record: RefreshSession) -> RefreshSession:
        self._session.add(record)
        await self._session.flush([record])
        return record

    async def revoke(self, jti: str) -> bool:
        stmt = (
            update(RefreshSession)
            .where(RefreshSession.jti == jti, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=_dt.datetime.now(_dt.UTC))
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount)

    async def revoke_for_user(self, user_id: str) -> int:
        stmt = (
            update(RefreshSession)
            .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=_dt.datetime.now(_dt.UTC))
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def list_active(self, user_id: str) -> list[RefreshSession]:
        from app.utils.datetime import utcnow

        now = utcnow()
        stmt = select(RefreshSession).where(
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > now,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())
