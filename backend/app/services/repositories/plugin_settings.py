"""Plugin settings repository."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin_settings import PluginSettings


class PluginSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_plugin(self, plugin_id: str) -> PluginSettings | None:
        result = await self._session.execute(
            select(PluginSettings).where(
                PluginSettings.plugin_id == plugin_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self, *, plugin_id: str, values: dict, notes: str | None = None
    ) -> PluginSettings:
        existing = await self.get_by_plugin(plugin_id)
        if existing is None:
            row = PluginSettings(
                plugin_id=plugin_id, values=values, notes=notes
            )
            self._session.add(row)
            await self._session.flush()
            return row
        existing.values = values
        if notes is not None:
            existing.notes = notes
        await self._session.flush()
        return existing

    async def list_all(self) -> Sequence[PluginSettings]:
        result = await self._session.execute(
            select(PluginSettings).order_by(PluginSettings.plugin_id)
        )
        return result.scalars().all()

    async def delete(self, plugin_id: str) -> None:
        row = await self.get_by_plugin(plugin_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
