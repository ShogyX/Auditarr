"""Notification channel + delivery repositories."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_channel import NotificationChannel
from app.models.notification_delivery import NotificationDelivery


class NotificationChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, channel: NotificationChannel) -> NotificationChannel:
        self._session.add(channel)
        await self._session.flush()
        return channel

    async def get(self, channel_id: str) -> NotificationChannel | None:
        return await self._session.get(NotificationChannel, channel_id)

    async def get_by_name(self, name: str) -> NotificationChannel | None:
        result = await self._session.execute(
            select(NotificationChannel).where(NotificationChannel.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self, *, enabled_only: bool = False
    ) -> Sequence[NotificationChannel]:
        stmt = select(NotificationChannel).order_by(NotificationChannel.name)
        if enabled_only:
            stmt = stmt.where(NotificationChannel.enabled.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete(self, channel: NotificationChannel) -> None:
        await self._session.delete(channel)
        await self._session.flush()


class NotificationDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, delivery: NotificationDelivery) -> NotificationDelivery:
        self._session.add(delivery)
        await self._session.flush()
        return delivery

    async def get(self, delivery_id: str) -> NotificationDelivery | None:
        return await self._session.get(NotificationDelivery, delivery_id)

    async def list_recent(
        self,
        *,
        channel_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> Sequence[NotificationDelivery]:
        stmt = (
            select(NotificationDelivery)
            .order_by(NotificationDelivery.attempted_at.desc())
            .limit(limit)
        )
        if channel_id:
            stmt = stmt.where(NotificationDelivery.channel_id == channel_id)
        if status:
            stmt = stmt.where(NotificationDelivery.status == status)
        result = await self._session.execute(stmt)
        return result.scalars().all()
