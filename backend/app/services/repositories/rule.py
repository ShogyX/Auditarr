"""Rule + RuleEvaluation repositories."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import datetime as _dt

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation


@dataclass(slots=True)
class RuleEvaluationFileSummary:
    """Stage 14b (audit follow-up): one evaluation row joined to
    its media file. Lightweight dataclass — used as the return type
    of :meth:`RuleEvaluationRepository.list_for_rule_with_files` and
    serialized by the API into :class:`RuleEvaluationFileRow`."""

    media_file_id: str
    library_id: str
    path: str
    filename: str
    severity: str
    severity_rank: int
    evaluated_at: _dt.datetime


class RuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, rule: Rule) -> Rule:
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def get(self, rule_id: str) -> Rule | None:
        return await self._session.get(Rule, rule_id)

    async def get_by_name(self, name: str) -> Rule | None:
        result = await self._session.execute(
            select(Rule).where(Rule.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, enabled_only: bool = False) -> Sequence[Rule]:
        stmt = select(Rule).order_by(Rule.priority, Rule.name)
        if enabled_only:
            stmt = stmt.where(Rule.enabled.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete(self, rule: Rule) -> None:
        await self._session.delete(rule)
        await self._session.flush()


class RuleEvaluationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, evaluation: RuleEvaluation) -> RuleEvaluation:
        """Insert or update keyed by (media_file_id, rule_id)."""
        existing = await self._session.execute(
            select(RuleEvaluation).where(
                RuleEvaluation.media_file_id == evaluation.media_file_id,
                RuleEvaluation.rule_id == evaluation.rule_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            self._session.add(evaluation)
            await self._session.flush()
            return evaluation
        row.severity = evaluation.severity
        row.severity_rank = evaluation.severity_rank
        row.actions_summary = evaluation.actions_summary
        row.evaluated_at = evaluation.evaluated_at
        await self._session.flush()
        return row

    async def delete_for_file(self, media_file_id: str) -> None:
        await self._session.execute(
            delete(RuleEvaluation).where(
                RuleEvaluation.media_file_id == media_file_id
            )
        )

    async def list_for_file(
        self, media_file_id: str
    ) -> Sequence[RuleEvaluation]:
        result = await self._session.execute(
            select(RuleEvaluation)
            .where(RuleEvaluation.media_file_id == media_file_id)
            .order_by(RuleEvaluation.severity_rank.desc())
        )
        return result.scalars().all()

    async def list_for_rule(
        self, rule_id: str, *, limit: int = 50
    ) -> Sequence[RuleEvaluation]:
        result = await self._session.execute(
            select(RuleEvaluation)
            .where(RuleEvaluation.rule_id == rule_id)
            .order_by(RuleEvaluation.evaluated_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_for_rule_with_files(
        self, rule_id: str, *, limit: int = 200
    ) -> list[RuleEvaluationFileSummary]:
        """Stage 14b (audit follow-up): per-rule evaluations joined
        to :class:`MediaFile`. Returns lightweight summary rows
        ordered by severity_rank desc then evaluated_at desc so the
        operator sees high-severity matches first.

        Files with no corresponding media row (orphaned by an
        eviction) are filtered out at the SQL layer via the inner
        join — they would 404 on click-through anyway.
        """
        stmt = (
            select(
                RuleEvaluation.media_file_id,
                MediaFile.library_id,
                MediaFile.path,
                MediaFile.filename,
                RuleEvaluation.severity,
                RuleEvaluation.severity_rank,
                RuleEvaluation.evaluated_at,
            )
            .join(MediaFile, MediaFile.id == RuleEvaluation.media_file_id)
            .where(RuleEvaluation.rule_id == rule_id)
            .order_by(
                RuleEvaluation.severity_rank.desc(),
                RuleEvaluation.evaluated_at.desc(),
            )
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            RuleEvaluationFileSummary(
                media_file_id=r[0],
                library_id=r[1],
                path=r[2],
                filename=r[3],
                severity=r[4],
                severity_rank=r[5],
                evaluated_at=r[6],
            )
            for r in rows
        ]
