"""RuleSuggestion repository (Stage 16 Turn 2)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule_suggestion import RuleSuggestion
from app.utils.datetime import utcnow

# Window after a "dismiss" during which we won't re-emit the same
# pattern. Tuned so an operator who dismisses "stop suggesting HEVC
# transcode rules" gets a full month of quiet — long enough that
# they've made their call, short enough that we'll revisit when the
# underlying playback pattern keeps mattering.
DISMISS_STICKY_DAYS = 30


class RuleSuggestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, suggestion: RuleSuggestion) -> RuleSuggestion:
        self._session.add(suggestion)
        await self._session.flush()
        return suggestion

    async def get(self, suggestion_id: str) -> RuleSuggestion | None:
        return await self._session.get(RuleSuggestion, suggestion_id)

    async def get_by_dedup_key(self, dedup_key: str) -> RuleSuggestion | None:
        result = await self._session.execute(
            select(RuleSuggestion).where(RuleSuggestion.dedup_key == dedup_key)
        )
        return result.scalar_one_or_none()

    async def list_pending(self) -> Sequence[RuleSuggestion]:
        """Pending suggestions, highest-confidence first.

        Used by the dashboard card. Confidence ties break by
        files_affected descending so the highest-impact suggestions
        surface first.
        """
        stmt = (
            select(RuleSuggestion)
            .where(RuleSuggestion.status == "pending")
            .order_by(
                RuleSuggestion.confidence.desc(),
                RuleSuggestion.files_affected.desc(),
                RuleSuggestion.created_at.desc(),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_all(self) -> Sequence[RuleSuggestion]:
        """All suggestions regardless of status. For diagnostics."""
        result = await self._session.execute(
            select(RuleSuggestion).order_by(RuleSuggestion.created_at.desc())
        )
        return result.scalars().all()

    async def has_recent_dismissal(self, dedup_key: str) -> bool:
        """True if the same dedup_key was dismissed within the sticky
        window. The analyzer consults this before inserting a new
        suggestion so a dismissed pattern stays dismissed."""
        result = await self._session.execute(
            select(RuleSuggestion).where(
                RuleSuggestion.dedup_key == dedup_key,
                RuleSuggestion.status == "dismissed",
                RuleSuggestion.dismissed_at
                >= utcnow() - _dt.timedelta(days=DISMISS_STICKY_DAYS),
            )
        )
        return result.scalar_one_or_none() is not None

    async def has_deployed(self, dedup_key: str) -> bool:
        """True if a suggestion with this dedup_key has already been
        deployed as a rule. The analyzer skips re-emitting it."""
        result = await self._session.execute(
            select(RuleSuggestion).where(
                RuleSuggestion.dedup_key == dedup_key,
                RuleSuggestion.status == "deployed",
            )
        )
        return result.scalar_one_or_none() is not None
