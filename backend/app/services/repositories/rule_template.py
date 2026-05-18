"""Rule template repository (v1.9 Stage 4.4).

Thin read-mostly accessor for the ``rule_templates`` table.
Write paths are limited to the seeder (which uses its own
session directly) and the "use template" API endpoint (which
creates a ``Rule`` row, not a ``RuleTemplate``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule_template import RuleTemplate


class RuleTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[RuleTemplate]:
        """Return all templates ordered by priority asc, name asc.

        Priority-asc matches the "Evaluation order" mental model
        operators learned from the Stage 4.5 side panel — when
        they pick a template to use, the priority number indicates
        roughly where the cloned rule will land in the queue.
        """
        rows = await self._session.execute(
            select(RuleTemplate).order_by(
                RuleTemplate.priority.asc(),
                RuleTemplate.name.asc(),
            )
        )
        return list(rows.scalars().all())

    async def get_by_id(self, template_id: str) -> RuleTemplate | None:
        return await self._session.get(RuleTemplate, template_id)


__all__ = ["RuleTemplateRepository"]
