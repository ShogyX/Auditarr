"""Library composition service (v1.9 Stage 3.3).

Powers ``GET /api/v1/dashboard/composition``. Replaces the
Stage 26 "Categories card = bar graph of two columns" model with a
structured panel: resolutions, top extensions, containers (with
normalized labels), subtitle formats / languages, audio languages,
unknown tracks, internal-vs-external subtitles, orphan count, and
a median-bitrate matrix per (resolution, codec, container,
library).

Every aggregation here is scoped to ``category == 'media'`` rows.
That's the v1.9 Stage 3.5 contract: a ``.nfo`` / ``.jpg`` / ``.srt``
sidecar should never inflate an "unprobed" count or a "tiny
container" row on this card. The legacy ``DashboardStats.categories``
method does NOT enforce this scope — it groups by ``video_codec``
and surfaces NULL as "unknown" without filtering by category. That's
a long-standing v1.7-era quirk; we leave it alone for the legacy
``/categories`` endpoint (operator-facing change risk) and enforce
the scope here at the new endpoint instead.

The full payload is shipped in one call so the UI doesn't have to
spray nine GETs at the server. Each section is a list of
``{key, label, count, total_size_bytes?}`` rows — uniform shape
makes the React renderer a single component over an array of
sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING

from sqlalchemy import and_, case, func, select

from app.models.library import Library
from app.models.media import MediaFile
from app.utils.container_label import container_label

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── Resolution buckets ──────────────────────────────────────────
#
# Mapped from the file's ``height`` column (NULL → "unknown"; 0 →
# "unknown"). The bucket boundaries are inclusive at the lower end:
# anything <480 lines is sub-SD; 480..719 is "480p" (SD/DVD); etc.
# 8K is a real bucket because anime / IMAX rips exist and the
# operator wants to see them as their own row, not bundled with 4K.
RESOLUTION_BUCKETS: list[tuple[str, str, int | None, int | None]] = [
    # (key, label, min_height, max_height_exclusive)
    ("lt480p", "<480p", 1, 480),
    ("480p", "480p", 480, 720),
    ("720p", "720p", 720, 1080),
    ("1080p", "1080p", 1080, 1440),
    ("1440p", "1440p", 1440, 2160),
    ("4k", "4K", 2160, 4320),
    ("8k", "8K", 4320, None),  # open-ended upper
]


@dataclass(slots=True)
class CompositionRow:
    """One row in any of the composition sections.

    ``total_size_bytes`` is omitted (left as 0) for sections where a
    size total doesn't carry meaning — language counts, for
    example, since the same file contributes to multiple language
    rows."""

    key: str
    label: str
    count: int
    total_size_bytes: int = 0


@dataclass(slots=True)
class BitrateMatrixRow:
    """One row in the median-bitrate matrix."""

    library_id: str | None
    library_name: str | None
    resolution_key: str
    """One of the RESOLUTION_BUCKETS keys, or "unknown"."""
    video_codec: str | None
    container: str | None
    """Normalized via ``container_label``."""
    file_count: int
    median_bitrate_kbps: int


@dataclass(slots=True)
class CompositionPayload:
    """The full composition response. Each top-level field is a
    section in the new Categories card."""

    resolutions: list[CompositionRow] = field(default_factory=list)
    extensions: list[CompositionRow] = field(default_factory=list)
    containers: list[CompositionRow] = field(default_factory=list)
    subtitle_formats: list[CompositionRow] = field(default_factory=list)
    subtitle_languages: list[CompositionRow] = field(default_factory=list)
    audio_languages: list[CompositionRow] = field(default_factory=list)
    unknown_tracks: dict[str, int] = field(default_factory=dict)
    """{"audio_unknown_count": int, "video_unknown_count": int}"""
    subtitles_internal_external: dict[str, int] = field(default_factory=dict)
    """{"internal": int, "external": int} where "internal" = file
    has a probed subtitle stream; "external" = sidecar .srt/.ass
    in the same library."""
    orphan_count: int = 0
    bitrate_matrix: list[BitrateMatrixRow] = field(default_factory=list)


class LibraryCompositionService:
    """Builds the full composition payload for a single library
    (or, when ``library_id`` is None, across every library)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(
        self, *, library_id: str | None = None
    ) -> CompositionPayload:
        scope = _media_scope(library_id)
        payload = CompositionPayload()
        payload.resolutions = await self._resolutions(scope)
        payload.extensions = await self._top_extensions(scope, limit=8)
        payload.containers = await self._containers(scope)
        payload.subtitle_formats = await self._subtitle_formats(scope)
        payload.subtitle_languages = await self._subtitle_languages(scope)
        payload.audio_languages = await self._audio_languages(scope)
        payload.unknown_tracks = await self._unknown_tracks(scope)
        payload.subtitles_internal_external = await self._subtitles_internal_external(
            library_id
        )
        payload.orphan_count = await self._orphan_count(library_id)
        payload.bitrate_matrix = await self._bitrate_matrix(scope)
        return payload

    # ── Resolutions ───────────────────────────────────────────
    async def _resolutions(self, scope) -> list[CompositionRow]:
        # Build a CASE that maps height → bucket key in one query.
        # Adding a Python-side aggregation pass would mean shipping
        # every (height, size) tuple to the app server — fine for a
        # 1000-file install, bad for a 200k-file one.
        whens = []
        for key, _label, lo, hi in RESOLUTION_BUCKETS:
            if hi is None:
                whens.append((MediaFile.height >= lo, key))
            elif lo is None:
                whens.append((MediaFile.height < hi, key))
            else:
                whens.append(
                    (and_(MediaFile.height >= lo, MediaFile.height < hi), key)
                )
        bucket = case(*whens, else_="unknown").label("bucket")

        stmt = (
            select(
                bucket,
                func.count(MediaFile.id),
                func.coalesce(func.sum(MediaFile.size_bytes), 0),
            )
            .where(*scope)
            .group_by(bucket)
        )
        rows = (await self._session.execute(stmt)).all()
        counts = {b: (int(c), int(s or 0)) for b, c, s in rows}
        # Emit rows in the canonical bucket order so the UI doesn't
        # have to re-order.
        out: list[CompositionRow] = []
        for key, label, _lo, _hi in RESOLUTION_BUCKETS:
            count, total = counts.get(key, (0, 0))
            if count == 0:
                continue
            out.append(
                CompositionRow(
                    key=key, label=label, count=count, total_size_bytes=total
                )
            )
        # Unknown bucket last, only if non-zero.
        if counts.get("unknown", (0, 0))[0] > 0:
            unknown_count, unknown_size = counts["unknown"]
            out.append(
                CompositionRow(
                    key="unknown",
                    label="Unknown",
                    count=unknown_count,
                    total_size_bytes=unknown_size,
                )
            )
        return out

    # ── Top extensions ────────────────────────────────────────
    async def _top_extensions(
        self, scope, *, limit: int
    ) -> list[CompositionRow]:
        stmt = (
            select(
                MediaFile.extension,
                func.count(MediaFile.id),
                func.coalesce(func.sum(MediaFile.size_bytes), 0),
            )
            .where(*scope)
            .group_by(MediaFile.extension)
            .order_by(func.count(MediaFile.id).desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            CompositionRow(
                key=(ext or "(none)").lower(),
                label=(ext or "(none)").lower(),
                count=int(c),
                total_size_bytes=int(s or 0),
            )
            for ext, c, s in rows
        ]

    # ── Containers (normalized labels) ────────────────────────
    async def _containers(self, scope) -> list[CompositionRow]:
        """Group by raw ``container`` column, then map each group's
        key through ``container_label`` so ``matroska`` and a future
        ``matroska,X`` row both collapse into ``MKV``. We do the
        grouping at the database (cheap) and the relabel+merge in
        Python (small N — typically <10 distinct values)."""
        stmt = (
            select(
                MediaFile.container,
                func.count(MediaFile.id),
                func.coalesce(func.sum(MediaFile.size_bytes), 0),
            )
            .where(*scope)
            .group_by(MediaFile.container)
        )
        rows = (await self._session.execute(stmt)).all()
        # Merge rows that map to the same label.
        merged: dict[str, tuple[int, int]] = {}
        for raw, count, total in rows:
            label = container_label(raw) or "Unknown"
            existing_c, existing_s = merged.get(label, (0, 0))
            merged[label] = (
                existing_c + int(count),
                existing_s + int(total or 0),
            )
        # Sort by total size descending.
        out = [
            CompositionRow(
                key=label.lower(),
                label=label,
                count=c,
                total_size_bytes=s,
            )
            for label, (c, s) in sorted(
                merged.items(), key=lambda kv: kv[1][1], reverse=True
            )
        ]
        return out

    # ── Subtitle formats (codec column) ───────────────────────
    async def _subtitle_formats(self, scope) -> list[CompositionRow]:
        """The MediaFile.subtitle_codec column holds the FIRST
        probed subtitle stream's codec (srt / ass / hdmv_pgs_subtitle
        / dvd_subtitle / etc.). Files with NO probed subtitle
        stream simply have NULL here; we drop NULLs since the
        section is about which subtitle codecs ARE present, not
        about counting files-without-subs."""
        stmt = (
            select(MediaFile.subtitle_codec, func.count(MediaFile.id))
            .where(*scope, MediaFile.subtitle_codec.isnot(None))
            .group_by(MediaFile.subtitle_codec)
            .order_by(func.count(MediaFile.id).desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            CompositionRow(
                key=str(codec).lower(),
                label=_subtitle_label(str(codec)),
                count=int(c),
            )
            for codec, c in rows
        ]

    # ── Subtitle / audio languages ────────────────────────────
    async def _subtitle_languages(self, scope) -> list[CompositionRow]:
        return await self._language_counts(scope, MediaFile.subtitle_languages)

    async def _audio_languages(self, scope) -> list[CompositionRow]:
        return await self._language_counts(scope, MediaFile.audio_languages)

    async def _language_counts(self, scope, column) -> list[CompositionRow]:
        """Languages live in a JSON list column. SQLAlchemy doesn't
        give us a portable "explode JSON array" primitive across
        sqlite + postgres, so we fetch the raw lists and aggregate
        in Python. The N here is bounded by the number of files in
        the library — which can be large, but each row's payload
        is tiny (3–5 ISO-639 codes), so we're streaming a few MB at
        worst.

        A scanner-side denormalization (per-(file, lang) row) would
        scale better; deferred until composition becomes a hot
        path."""
        stmt = select(column).where(*scope, column.isnot(None))
        rows = (await self._session.execute(stmt)).all()
        counts: dict[str, int] = {}
        for (langs,) in rows:
            if not langs:
                continue
            for lang in langs:
                if not isinstance(lang, str) or not lang:
                    continue
                key = lang.strip().lower()
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
        # Sort by descending count.
        return [
            CompositionRow(key=k, label=k, count=v)
            for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]

    # ── Unknown audio / video tracks ──────────────────────────
    async def _unknown_tracks(self, scope) -> dict[str, int]:
        """A "track" here means an ffprobe-reported stream whose
        ``codec_name`` was NULL or empty — distinct from the file
        itself failing to probe. We approximate by counting rows
        where audio_codec / video_codec is NULL among PROBED files
        (probe_failed = false). The actual per-stream walk would
        need to introspect ``probe`` JSON; this scalar approximation
        is good enough for the dashboard and avoids streaming every
        probe blob."""
        # video_unknown: probed (probe_failed=False) but video_codec NULL.
        # audio_unknown: probed but audio_codec NULL.
        stmt_video = select(func.count(MediaFile.id)).where(
            *scope,
            MediaFile.probe_failed.is_(False),
            MediaFile.video_codec.is_(None),
        )
        stmt_audio = select(func.count(MediaFile.id)).where(
            *scope,
            MediaFile.probe_failed.is_(False),
            MediaFile.audio_codec.is_(None),
        )
        video_unknown = int(
            (await self._session.execute(stmt_video)).scalar() or 0
        )
        audio_unknown = int(
            (await self._session.execute(stmt_audio)).scalar() or 0
        )
        return {
            "video_unknown_count": video_unknown,
            "audio_unknown_count": audio_unknown,
        }

    # ── Internal vs external subtitles ────────────────────────
    async def _subtitles_internal_external(
        self, library_id: str | None
    ) -> dict[str, int]:
        """
        * **Internal** = media file has at least one probed subtitle
          stream (``has_subtitles == True``).
        * **External** = sidecar subtitle file in the index (category
          == "subtitle"). Same library scoping applies.

        Both counts respect ``library_id`` when provided.
        """
        internal_stmt = select(func.count(MediaFile.id)).where(
            MediaFile.category == "media",
            MediaFile.has_subtitles.is_(True),
        )
        external_stmt = select(func.count(MediaFile.id)).where(
            MediaFile.category == "subtitle",
        )
        if library_id is not None:
            internal_stmt = internal_stmt.where(
                MediaFile.library_id == library_id
            )
            external_stmt = external_stmt.where(
                MediaFile.library_id == library_id
            )
        internal = int(
            (await self._session.execute(internal_stmt)).scalar() or 0
        )
        external = int(
            (await self._session.execute(external_stmt)).scalar() or 0
        )
        return {"internal": internal, "external": external}

    # ── Orphan count ──────────────────────────────────────────
    async def _orphan_count(self, library_id: str | None) -> int:
        stmt = select(func.count(MediaFile.id)).where(
            MediaFile.is_orphaned.is_(True),
            MediaFile.category == "media",
        )
        if library_id is not None:
            stmt = stmt.where(MediaFile.library_id == library_id)
        return int((await self._session.execute(stmt)).scalar() or 0)

    # ── Bitrate matrix ────────────────────────────────────────
    async def _bitrate_matrix(self, scope) -> list[BitrateMatrixRow]:
        """Per-(library, resolution_bucket, video_codec, container)
        cell: median bitrate + file count.

        Median doesn't have a portable SQL primitive across sqlite
        and postgres — postgres has ``percentile_cont``, sqlite
        doesn't. We fetch the raw (cell_key, bitrate) pairs and
        compute the median in Python. The cardinality is bounded
        (small N of distinct cells: ~5 resolutions × ~5 codecs × ~5
        containers × ~5 libraries = 625 cells max in practice), so
        the worst-case payload is reasonable.

        Cells with fewer than 3 files are dropped — a 1-file
        "median" isn't actionable, and the matrix should be a
        scanning aid, not a per-file dump."""
        bucket_whens = []
        for key, _label, lo, hi in RESOLUTION_BUCKETS:
            if hi is None:
                bucket_whens.append((MediaFile.height >= lo, key))
            elif lo is None:
                bucket_whens.append((MediaFile.height < hi, key))
            else:
                bucket_whens.append(
                    (and_(MediaFile.height >= lo, MediaFile.height < hi), key)
                )
        bucket = case(*bucket_whens, else_="unknown").label("bucket")

        stmt = (
            select(
                MediaFile.library_id,
                Library.name,
                bucket,
                MediaFile.video_codec,
                MediaFile.container,
                MediaFile.bitrate_kbps,
            )
            .join(Library, Library.id == MediaFile.library_id, isouter=True)
            .where(*scope, MediaFile.bitrate_kbps.isnot(None))
        )
        rows = (await self._session.execute(stmt)).all()

        # Group by cell key (Python-side bucketing of the bitrate
        # samples).
        groups: dict[
            tuple[str | None, str | None, str, str | None, str | None],
            list[int],
        ] = {}
        for lib_id, lib_name, bkt, vcodec, container, br in rows:
            cell = (lib_id, lib_name, str(bkt), vcodec, container)
            groups.setdefault(cell, []).append(int(br))

        out: list[BitrateMatrixRow] = []
        for (lib_id, lib_name, bkt, vcodec, container), samples in groups.items():
            if len(samples) < 3:
                continue
            out.append(
                BitrateMatrixRow(
                    library_id=lib_id,
                    library_name=lib_name,
                    resolution_key=bkt,
                    video_codec=vcodec,
                    container=container_label(container),
                    file_count=len(samples),
                    median_bitrate_kbps=int(median(samples)),
                )
            )
        # Sort by file_count descending — operator wants to see the
        # densest cells first.
        out.sort(key=lambda r: r.file_count, reverse=True)
        return out


# ── Helpers ────────────────────────────────────────────────────


def _media_scope(library_id: str | None) -> list:
    """Common WHERE-clause fragments for every composition query.

    v1.9 Stage 3.5: ``category == 'media'`` is the scope. Sidecar
    files (.srt / .nfo / .jpg / .DS_Store) should never feature in
    a composition row — they're not media; counting them as
    "unknown codec" would mislead the operator. The subtitle
    section separately joins the "subtitle" category for the
    external-subtitle count."""
    scope: list = [MediaFile.category == "media"]
    if library_id is not None:
        scope.append(MediaFile.library_id == library_id)
    return scope


_SUBTITLE_LABELS = {
    "subrip": "SRT",
    "srt": "SRT",
    "ass": "ASS",
    "ssa": "SSA",
    "webvtt": "VTT",
    "hdmv_pgs_subtitle": "PGS",
    "dvd_subtitle": "VobSub",
    "dvb_subtitle": "DVB",
    "mov_text": "MOV_TEXT",
}


def _subtitle_label(codec: str) -> str:
    """Map a raw subtitle codec name to a friendlier label."""
    return _SUBTITLE_LABELS.get(codec.strip().lower(), codec.upper())


__all__ = [
    "LibraryCompositionService",
    "CompositionPayload",
    "CompositionRow",
    "BitrateMatrixRow",
    "RESOLUTION_BUCKETS",
]
