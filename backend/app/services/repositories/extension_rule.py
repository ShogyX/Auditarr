"""Media extension rule repository (Stage 9 audit follow-up)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extension_rule import MediaExtensionRule


class MediaExtensionRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(
        self, *, enabled_only: bool = False
    ) -> Sequence[MediaExtensionRule]:
        stmt = select(MediaExtensionRule)
        if enabled_only:
            stmt = stmt.where(MediaExtensionRule.enabled.is_(True))
        stmt = stmt.order_by(MediaExtensionRule.extension.asc())
        return (await self._session.execute(stmt)).scalars().all()

    async def get(self, rule_id: str) -> MediaExtensionRule | None:
        return await self._session.get(MediaExtensionRule, rule_id)

    async def get_by_extension(
        self, extension: str
    ) -> MediaExtensionRule | None:
        stmt = select(MediaExtensionRule).where(
            MediaExtensionRule.extension == extension
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add(
        self, rule: MediaExtensionRule
    ) -> MediaExtensionRule:
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def delete(self, rule: MediaExtensionRule) -> None:
        await self._session.delete(rule)
        await self._session.flush()

    async def load_disposition_map(self) -> dict[str, str]:
        """Return ``{extension: disposition}`` for every enabled row.

        The scanner reads this at scan-start so an O(1) dict lookup
        per file replaces the per-row DB query. The map is small —
        operators rarely create more than a few dozen rules.
        """
        rows = await self.list_all(enabled_only=True)
        return {r.extension: r.disposition for r in rows}
