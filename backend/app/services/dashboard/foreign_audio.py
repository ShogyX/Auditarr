"""v1.9 Stage 9.5.7 (OP-8) — Foreign-audio dashboard surface.

Counts media files whose primary audio language is NOT in the
operator's ``preferred_audio_languages`` AND that carry no
subtitle track in any of the operator's
``preferred_subtitle_languages``.

The matcher works in Python over the two JSON list columns on
MediaFile (``audio_languages`` / ``subtitle_languages``). SQLite
doesn't support a portable "any element of JSON array IN list"
predicate, so a stream-and-filter approach is simpler and stays
correct across the two backends we ship on (SQLite for dev /
single-node deployments, Postgres for production). Scale: at
100k media files the filter is a single linear scan over the
result set; well under the request timeout. We cap the
materialized set at 50k for the count + the first 10 sample
ids, which is enough for the dashboard's "tile + drill-in" UX
without putting the row count in the response on the critical
path of a request that's already O(N) on the python side.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.models.media import MediaFile
from app.schemas.dashboard import ForeignAudioSummaryRead


class ForeignAudioService:
    """Compute the dashboard's foreign-audio summary."""

    # Cap the materialized set so the request stays bounded
    # regardless of library size. Pre-1.9 surfaces also use a
    # 50k cap (see DashboardStats.composition).
    _LIBRARY_SCAN_CAP = 50_000

    def __init__(
        self, *, session: AsyncSession, settings: Settings
    ) -> None:
        self._session = session
        self._settings = settings

    async def summary(self) -> ForeignAudioSummaryRead:
        prefer_audio = {
            s.lower()
            for s in (self._settings.preferred_audio_languages or [])
            if s
        }
        prefer_subs = {
            s.lower()
            for s in (self._settings.preferred_subtitle_languages or [])
            if s
        }

        # Empty preferences ⇒ operator hasn't asked for any
        # filtering. Return zero so the tile renders empty +
        # explains the config nudge.
        if not prefer_audio:
            return ForeignAudioSummaryRead(
                count=0,
                sample_ids=[],
                preferred_audio_languages=sorted(prefer_audio),
                preferred_subtitle_languages=sorted(prefer_subs),
            )

        # Filter to media files only (sidecars don't carry audio
        # tracks anyway, but the explicit filter is cheap and
        # avoids surprise). The bare-minimum select keeps the
        # row dict small — id + the two JSON columns.
        result = await self._session.execute(
            select(
                MediaFile.id,
                MediaFile.audio_languages,
                MediaFile.subtitle_languages,
            )
            .where(MediaFile.category == "media")
            .limit(self._LIBRARY_SCAN_CAP)
        )

        sample_ids: list[str] = []
        count = 0
        for row in result.all():
            file_id, audio_langs, sub_langs = row
            if not _has_foreign_primary_audio(audio_langs, prefer_audio):
                continue
            if _has_preferred_subtitle(sub_langs, prefer_subs):
                continue
            count += 1
            if len(sample_ids) < 10:
                sample_ids.append(file_id)

        return ForeignAudioSummaryRead(
            count=count,
            sample_ids=sample_ids,
            preferred_audio_languages=sorted(prefer_audio),
            preferred_subtitle_languages=sorted(prefer_subs),
        )


def _has_foreign_primary_audio(
    audio_langs: list[str] | None, prefer: set[str]
) -> bool:
    """Return True if the file's primary (first) audio track is
    NOT in ``prefer``.

    Edge cases:
      * ``audio_langs`` is None or empty → we can't say it's
        foreign; treat as non-matching (operator's interested in
        FILES we KNOW are foreign; "unknown" is a separate
        category surfaced by the Unknown-tracks Categories
        section).
      * First entry is the canonical "und" / "unknown" / empty
        string → also non-matching (we don't have signal).
    """
    if not audio_langs:
        return False
    primary = str(audio_langs[0] or "").strip().lower()
    if primary in ("", "und", "unknown"):
        return False
    return primary not in prefer


def _has_preferred_subtitle(
    sub_langs: list[str] | None, prefer: set[str]
) -> bool:
    """Return True if any of the file's subtitle tracks carries
    a language in ``prefer``.

    Empty preference set ⇒ no subtitle saves the file. Empty
    subtitle list ⇒ no save."""
    if not prefer:
        return False
    if not sub_langs:
        return False
    for lang in sub_langs:
        normalized = str(lang or "").strip().lower()
        if normalized in prefer:
            return True
    return False


def _empty_summary(
    prefer_audio: set[str], prefer_subs: set[str]
) -> dict[str, Any]:
    """Internal helper for the empty-config short-circuit. Kept
    for any future caller that needs the dict shape without the
    response model."""
    return {
        "count": 0,
        "sample_ids": [],
        "preferred_audio_languages": sorted(prefer_audio),
        "preferred_subtitle_languages": sorted(prefer_subs),
    }


__all__ = [
    "ForeignAudioService",
    "_has_foreign_primary_audio",
    "_has_preferred_subtitle",
]
