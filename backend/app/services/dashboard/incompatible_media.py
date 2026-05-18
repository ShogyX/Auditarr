"""v1.9 Stage 9.5.7 (OP-9) — Incompatible-media dashboard surface.

Counts media files carrying at least one tag whose name matches
the incompatibility convention ``*-incompatible-*`` (so
operator-authored rules with their own tag prefixes — ``plex``,
``jellyfin``, ``my-target`` — all surface together). The
matching rules are operator-authored via the existing rule
editor; this surface just aggregates the tag-set the rules
produce.

Implementation: a single SQL count of distinct
``media_file_id`` from ``media_tags`` where the tag name's
slug contains ``incompatible``. We use a LIKE pattern rather
than a hardcoded list of tag names so a future rule that
introduces ``radarr-incompatible-container`` (or anything else
the operator dreams up) shows up here automatically.
"""

from __future__ import annotations

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import MediaTag
from app.schemas.dashboard import IncompatibleMediaSummaryRead


class IncompatibleMediaService:
    """Compute the dashboard's incompatible-media summary."""

    # The convention: any tag carrying the substring
    # "incompatible" anywhere in its name counts. Built-in rules
    # use ``plex-incompatible-video`` / ``plex-incompatible-audio``
    # / ``jellyfin-incompatible-video``; operator-authored rules
    # can extend the prefix list freely.
    _MATCH_PATTERN = "%incompatible%"

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def summary(self) -> IncompatibleMediaSummaryRead:
        # COUNT(DISTINCT media_file_id) over MediaTag rows whose
        # name contains "incompatible". The LIKE works on both
        # SQLite and Postgres without a dialect-specific cast.
        count_stmt = select(
            func.count(distinct(MediaTag.media_file_id))
        ).where(MediaTag.name.ilike(self._MATCH_PATTERN))
        count_result = await self._session.execute(count_stmt)
        count = int(count_result.scalar_one() or 0)

        # Sample IDs — first 10 distinct file ids ordered by the
        # most-recently-tagged. ``created_at`` lives on the tag
        # row (TimestampMixin); the order isn't semantically
        # critical, just "give the operator something to drill
        # into right now."
        sample_stmt = (
            select(MediaTag.media_file_id)
            .where(MediaTag.name.ilike(self._MATCH_PATTERN))
            .order_by(MediaTag.created_at.desc())
            .limit(50)
        )
        sample_result = await self._session.execute(sample_stmt)
        seen: list[str] = []
        seen_set: set[str] = set()
        for row in sample_result.all():
            fid = row[0]
            if fid in seen_set:
                continue
            seen_set.add(fid)
            seen.append(fid)
            if len(seen) >= 10:
                break

        return IncompatibleMediaSummaryRead(count=count, sample_ids=seen)


__all__ = ["IncompatibleMediaService"]
