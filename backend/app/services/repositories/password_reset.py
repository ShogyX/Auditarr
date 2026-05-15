"""Password-reset token repository."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset import PasswordResetToken


class PasswordResetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, token_hash: str) -> PasswordResetToken | None:
        return await self._session.get(PasswordResetToken, token_hash)

    async def add(self, record: PasswordResetToken) -> PasswordResetToken:
        self._session.add(record)
        await self._session.flush([record])
        return record

    async def mark_used(self, record: PasswordResetToken) -> None:
        record.used_at = _dt.datetime.now(_dt.UTC)
        await self._session.flush([record])

    async def delete_for_user(self, user_id: str) -> None:
        stmt = delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
        await self._session.execute(stmt)
