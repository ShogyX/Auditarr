"""MediaFile repository.

Provides paginated reads with filterable predicates and the upsert path
used by the scanner (look up by absolute path, update if found, insert
otherwise).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.tag import MediaTag


@dataclass(slots=True)
class MatchedRuleSummary:
    """A single (rule_id, rule_name, severity) tuple for a file.

    Stage 3 (audit follow-up): the Files table needs to render which
    rules matched a row without per-row fetches. The summary is
    intentionally minimal — just enough for a chip strip.
    """

    rule_id: str
    rule_name: str
    severity: str


@dataclass(slots=True)
class MediaFilter:
    library_id: str | None = None
    category: str | None = None
    severity: str | None = None
    extension: str | None = None
    is_orphaned: bool | None = None
    # Stage 27 added a quarantine filter (``quarantined: bool | None``).
    # Stage 05 (v1.7) retired the quarantine workflow — "delete means
    # delete" (Section A.0). The filter is gone with it; callers
    # that used to pass ``quarantined=False`` for the default Files
    # view now get the same behaviour for free since no row carries
    # that state any more.
    search: str | None = None  # substring match against path/filename
    # Stage 23: sortable column + direction. ``sort`` must be one of the
    # whitelisted keys below; anything else falls back to the legacy order
    # (severity_rank desc, path asc). The whitelist exists because the
    # column has to be both indexed and safe to expose — we don't want a
    # caller asking us to sort by ``probe`` (a JSON blob).
    sort: str | None = None
    sort_dir: str = "desc"
    # Stage 31: codec + container filters. Both accept a
    # comma-separated list of values; multi-value uses IN, single
    # uses equality. The motivation is a direct drill-down from
    # the dashboard "categories" panel (which already groups by
    # video_codec and container) into the Files page — "I see a
    # codec spike on the dashboard → show me those files."
    #
    # ``None`` and empty string both mean "no filter on this
    # column"; the API endpoint normalizes incoming query strings
    # to that.
    #
    # Codec / container values come from ffprobe, so they're
    # already lowercased and stable; we don't lowercase again
    # here (unlike ``extension``) because incoming values from
    # the UI come straight off probed rows.
    video_codec: str | None = None
    container: str | None = None
    # Stage 3 (audit follow-up): scope tri-state.
    #   - None / "all" → no scope filter (legacy behaviour)
    #   - "media"     → only rows with category == "media"
    #   - "non-media" → only rows with category != "media"
    # Distinct from ``category`` (which exposes an exact-string
    # equality filter) so the operator can scope to "everything
    # non-media" without enumerating the specific non-media
    # categories.
    scope: Literal["all", "media", "non-media"] | None = None
    # Stage 3 (audit follow-up): empty-severity-filter sentinel.
    #
    # The frontend lets the operator toggle individual severity chips.
    # When the active set is empty (operator hit "hide all"), the page
    # MUST return zero rows — currently it returns everything because
    # ``severity=""`` collapses to "no filter". This flag distinguishes
    # "no filter requested" (None on both) from "filter is the empty
    # set" (severities_empty=True) so the repo can apply a sentinel
    # ``WHERE 1=0`` predicate. Pre-Stage-3 callers don't pass it and
    # behaviour is unchanged for them.
    severities_empty: bool = False
    # Stage 3 (audit follow-up): optionally attach the matched-rules
    # summary to each returned row. Defaults off because the join is
    # not free; the Files page turns it on, the dashboard's
    # severity-rollup queries leave it off.
    include_matched_rules: bool = False
    # Stage 13 (audit follow-up): optionally attach the tag names per
    # row. Defaults off because the LEFT JOIN onto ``media_tags`` is
    # not free; the Files page turns it on when the optional "tags"
    # column is enabled. Returns a deduped list of tag NAMES — the
    # source distinction is preserved in the dedicated
    # ``/media/{id}/tags`` endpoint where the drawer fetches richer
    # info anyway.
    include_tags: bool = False
    # Stage 18 (audit follow-up): "files tagged with any of these
    # names" filter. ``None`` and empty list both mean "no filter"
    # — the alternative (empty list = match nothing) would conflate
    # "I didn't pick any tags" with "I picked zero tags on purpose"
    # and silently zero out an automation. The OR semantics (any-of,
    # not all-of) is what the automation surface needs: "run on
    # anything tagged sonarr OR radarr". A future ``tags_all`` flag
    # could add AND semantics if needed; one knob at a time.
    tags_any: list[str] | None = None
    # Stage 02 (v1.7): per-column quick filters from the Files
    # table's optional filter row. All eight are independent
    # predicates ANDed with the rest of the WHERE.
    #
    # ``path_contains`` — case-insensitive substring on ``path``.
    # Complements ``search`` (which also substring-matches path)
    # so two distinct UI inputs can coexist without one
    # invalidating the other.
    #
    # ``codec_contains`` — case-insensitive substring on
    # ``video_codec``. Useful for the operator who types ``hev`` to
    # match both ``hevc`` and any future ``hevc-*`` variant.
    #
    # ``container_eq`` / ``extension_eq`` — strict equality.
    # Container and extension are short closed-set values where
    # substring noise hurts.
    #
    # ``size_min`` / ``size_max`` — inclusive byte range. The UI
    # for these arrives later; the contract ships now so the
    # backend can be wired and tested without a follow-up.
    #
    # ``mtime_after`` / ``mtime_before`` — inclusive datetime
    # range against ``MediaFile.mtime``. ISO 8601 strings on the
    # wire; converted at the API boundary to datetime objects.
    path_contains: str | None = None
    codec_contains: str | None = None
    container_eq: str | None = None
    extension_eq: str | None = None
    size_min: int | None = None
    size_max: int | None = None
    mtime_after: object | None = None  # datetime | None
    mtime_before: object | None = None  # datetime | None


SORTABLE_COLUMNS: tuple[str, ...] = (
    "path",
    "filename",
    "size_bytes",
    "mtime",
    "severity_rank",
    "category",
    "extension",
    "seen_at",
    # Stage 3 (audit follow-up): three new sortable columns.
    # ``severity`` is the label string; ``severity_rank`` was already
    # sortable as the rank int, but the column header on the Files
    # page sends the human key. They sort in the same order (rank
    # mirrors label) so the alias is safe.
    "severity",
    "video_codec",
    "container",
)


@dataclass(slots=True)
class MediaPage:
    items: list[MediaFile]
    total: int
    offset: int
    limit: int
    # Stage 3 (audit follow-up): when ``include_matched_rules`` is set
    # on the filter, this map carries the matched-rules list per file
    # id. Keys are absent for files with no matching rules; the API
    # serializer treats missing entries as an empty list.
    matched_rules: dict[str, list[MatchedRuleSummary]] = field(
        default_factory=dict
    )
    # Stage 13 (audit follow-up): per-file tag names, populated when
    # ``filt.include_tags`` is set. Keys are absent for files with
    # no tags; the API serializer treats missing entries as an empty
    # list. Source-distinction is intentionally NOT carried here —
    # the table column shows tag names only; the drawer fetches the
    # richer per-source breakdown via ``/media/{id}/tags``.
    tags: dict[str, list[str]] = field(default_factory=dict)


# Map the publicly-exposed "severity" sort key to the underlying
# rank column. The label column would sort alphabetically (which
# isn't what the operator wants — they want "crit > error > high
# > warn > info > ok"), so we always sort by rank under the hood
# regardless of which alias the caller asked for.
_SORT_COLUMN_ALIASES: dict[str, str] = {
    "severity": "severity_rank",
}


class MediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, media_id: str) -> MediaFile | None:
        return await self._session.get(MediaFile, media_id)

    async def get_by_path(self, path: str) -> MediaFile | None:
        result = await self._session.execute(
            select(MediaFile).where(MediaFile.path == path)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        filt: MediaFilter | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> MediaPage:
        filt = filt or MediaFilter()
        conditions = []
        # Stage 3 (audit follow-up): if the caller sent the
        # empty-severity-set sentinel, short-circuit to a zero-row
        # response. Doing this BEFORE building the rest of the
        # query (and especially before the count query) means the
        # database sees a trivially-empty plan and the page is
        # consistent (total=0, items=[]).
        if filt.severities_empty:
            return MediaPage(items=[], total=0, offset=offset, limit=limit)
        if filt.library_id:
            conditions.append(MediaFile.library_id == filt.library_id)
        if filt.category:
            conditions.append(MediaFile.category == filt.category)
        # Stage 3: scope tri-state. Applied independently of the exact
        # ``category`` filter; if both are set, the more specific one
        # (category equality) wins because it's a strict subset of any
        # scope value.
        if filt.scope == "media":
            conditions.append(MediaFile.category == "media")
        elif filt.scope == "non-media":
            conditions.append(MediaFile.category != "media")
        if filt.severity:
            # Stage 14.1: support comma-separated severity (e.g. ``warn,high``)
            # so the Files scope bar can narrow to a subset. Single-value
            # callers still work — a string without commas just becomes a
            # 1-element IN clause.
            severities = [s.strip() for s in filt.severity.split(",") if s.strip()]
            if len(severities) == 1:
                conditions.append(MediaFile.severity == severities[0])
            elif len(severities) > 1:
                conditions.append(MediaFile.severity.in_(severities))
        if filt.extension:
            conditions.append(MediaFile.extension == filt.extension.lower())
        if filt.is_orphaned is not None:
            conditions.append(MediaFile.is_orphaned.is_(filt.is_orphaned))
        # Stage 27's ``quarantined`` predicate was removed in Stage 05
        # (Section A.0) — the column is gone, the filter is gone.
        # Stage 31: codec + container filters. Comma-separated
        # values become IN clauses; single values become equality.
        # Empty string after split (e.g. "h264,") is silently
        # dropped — we treat the UI as a source of truth that
        # may include a trailing comma after deselection.
        if filt.video_codec:
            codecs = [
                c.strip()
                for c in filt.video_codec.split(",")
                if c.strip()
            ]
            if len(codecs) == 1:
                conditions.append(MediaFile.video_codec == codecs[0])
            elif len(codecs) > 1:
                conditions.append(MediaFile.video_codec.in_(codecs))
        if filt.container:
            containers = [
                c.strip()
                for c in filt.container.split(",")
                if c.strip()
            ]
            if len(containers) == 1:
                conditions.append(MediaFile.container == containers[0])
            elif len(containers) > 1:
                conditions.append(MediaFile.container.in_(containers))
        if filt.search:
            like = f"%{filt.search.lower()}%"
            conditions.append(func.lower(MediaFile.path).like(like))

        # Stage 18 (audit follow-up): ``tags_any`` selects files
        # carrying ANY of the listed tag names. Implemented as a
        # correlated EXISTS subquery so the join doesn't multiply
        # rows (a JOIN over media_tags would). Empty list and None
        # both mean "no filter" — see the field comment for why.
        if filt.tags_any:
            tag_names = [t.strip() for t in filt.tags_any if t and t.strip()]
            if tag_names:
                conditions.append(
                    select(MediaTag.media_file_id)
                    .where(
                        MediaTag.media_file_id == MediaFile.id,
                        MediaTag.name.in_(tag_names),
                    )
                    .exists()
                )

        # Stage 02 (v1.7): per-column quick filters. All
        # case-insensitive where they're substring filters; strict
        # equality where the value space is small. Each predicate
        # is independent and ANDed with the rest of the WHERE so
        # an operator can combine "path contains 1080" with
        # "codec contains hevc" without surprise interactions.
        if filt.path_contains and filt.path_contains.strip():
            needle = filt.path_contains.strip().lower()
            conditions.append(func.lower(MediaFile.path).like(f"%{needle}%"))
        if filt.codec_contains and filt.codec_contains.strip():
            needle = filt.codec_contains.strip().lower()
            # ``video_codec`` is nullable; the LIKE comparison is
            # short-circuited on NULL by SQL so we don't need an
            # explicit IS NOT NULL guard.
            conditions.append(
                func.lower(MediaFile.video_codec).like(f"%{needle}%")
            )
        if filt.container_eq and filt.container_eq.strip():
            conditions.append(MediaFile.container == filt.container_eq.strip())
        if filt.extension_eq and filt.extension_eq.strip():
            # ``extension`` is stored lowercased and WITHOUT the
            # leading dot (see ``services/media/scanner.py`` —
            # ``abs_path.suffix.lstrip(".").lower()``). The
            # operator's input may carry the dot ("``.mkv``")
            # because that's the human convention; normalise so
            # both forms match.
            needle = filt.extension_eq.strip().lstrip(".").lower()
            conditions.append(MediaFile.extension == needle)
        if filt.size_min is not None:
            conditions.append(MediaFile.size_bytes >= filt.size_min)
        if filt.size_max is not None:
            conditions.append(MediaFile.size_bytes <= filt.size_max)
        if filt.mtime_after is not None:
            conditions.append(MediaFile.mtime >= filt.mtime_after)
        if filt.mtime_before is not None:
            conditions.append(MediaFile.mtime <= filt.mtime_before)

        where = and_(*conditions) if conditions else None

        count_stmt = select(func.count()).select_from(MediaFile)
        if where is not None:
            count_stmt = count_stmt.where(where)
        total = (await self._session.execute(count_stmt)).scalar_one()

        list_stmt = select(MediaFile)
        # Stage 23: ordered column choice. The default order
        # (severity-first then path) is still the right answer when no
        # explicit sort is requested — it surfaces the most-urgent items
        # at the top, which is what every dashboard drilldown wants.
        if filt.sort and filt.sort in SORTABLE_COLUMNS:
            # Stage 3: resolve alias keys (e.g. ``severity`` → ``severity_rank``)
            # before hitting the ORM. The whitelist guarantees the input
            # is safe; the alias map is a tiny, fixed translation.
            effective = _SORT_COLUMN_ALIASES.get(filt.sort, filt.sort)
            column = getattr(MediaFile, effective)
            direction = column.desc() if filt.sort_dir == "desc" else column.asc()
            # Always include a stable secondary sort by ``path`` so two
            # rows with identical primary values come back in a
            # deterministic order — critical for keyset-free offset
            # pagination, otherwise the same row can flicker between
            # pages on adjacent requests.
            list_stmt = list_stmt.order_by(direction, MediaFile.path)
        else:
            list_stmt = list_stmt.order_by(
                MediaFile.severity_rank.desc(), MediaFile.path
            )
        list_stmt = list_stmt.offset(offset).limit(limit)
        if where is not None:
            list_stmt = list_stmt.where(where)
        items = list((await self._session.execute(list_stmt)).scalars().all())

        # Stage 3 (audit follow-up): attach matched-rules summary in a
        # single grouped query rather than one fetch per row. We avoid
        # JSON-AGG / JSONB-AGG entirely so the same code path works on
        # SQLite (used in the test suite) and Postgres (production)
        # without dialect-specific SQL.
        matched: dict[str, list[MatchedRuleSummary]] = {}
        if filt.include_matched_rules and items:
            ids = [m.id for m in items]
            rows = (
                await self._session.execute(
                    select(
                        RuleEvaluation.media_file_id,
                        RuleEvaluation.rule_id,
                        Rule.name,
                        RuleEvaluation.severity,
                        RuleEvaluation.severity_rank,
                    )
                    .join(Rule, Rule.id == RuleEvaluation.rule_id)
                    .where(RuleEvaluation.media_file_id.in_(ids))
                    .order_by(
                        RuleEvaluation.media_file_id,
                        RuleEvaluation.severity_rank.desc(),
                        Rule.name,
                    )
                )
            ).all()
            for media_file_id, rule_id, rule_name, severity, _rank in rows:
                matched.setdefault(media_file_id, []).append(
                    MatchedRuleSummary(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        severity=severity,
                    )
                )

        # Stage 13 (audit follow-up): tag names per row via a single
        # grouped query. Deliberately separate from the matched-rules
        # join to keep both paths independently togglable — most
        # callers want one OR the other, not both. ORDER BY name so
        # the chip strip in the table renders deterministic order.
        tags_map: dict[str, list[str]] = {}
        if filt.include_tags and items:
            ids = [m.id for m in items]
            tag_rows = (
                await self._session.execute(
                    select(
                        MediaTag.media_file_id,
                        MediaTag.name,
                    )
                    .where(MediaTag.media_file_id.in_(ids))
                    .order_by(MediaTag.media_file_id, MediaTag.name)
                )
            ).all()
            for media_file_id, name in tag_rows:
                bucket = tags_map.setdefault(media_file_id, [])
                # Dedupe in-place: a file can have the same tag NAME
                # from two sources ("4K" from Sonarr + "4K" from a
                # rule). The table column shows names only so we
                # collapse duplicates here.
                if name not in bucket:
                    bucket.append(name)

        return MediaPage(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
            matched_rules=matched,
            tags=tags_map,
        )

    async def upsert_by_path(self, mf: MediaFile) -> MediaFile:
        """Insert or refresh a media file keyed by absolute path."""
        existing = await self.get_by_path(mf.path)
        if existing is None:
            self._session.add(mf)
            await self._session.flush()
            return mf
        # Update mutable fields in place. Identity keys (id, path) stay put.
        for col in (
            "library_id", "relative_path", "filename", "extension",
            "size_bytes", "mtime", "inode",
            "category",
            "container", "duration_seconds", "bitrate_kbps",
            "video_codec", "audio_codec", "subtitle_codec",
            "width", "height", "framerate",
            "has_subtitles", "subtitle_languages", "audio_languages",
            "probe", "probe_failed", "probe_error",
            "last_scan_id", "seen_at", "is_orphaned",
        ):
            setattr(existing, col, getattr(mf, col))
        await self._session.flush()
        return existing

    async def mark_orphans(
        self, library_id: str, *, last_scan_id: str
    ) -> int:
        """Flag every file in a library not touched by the latest scan."""
        result = await self._session.execute(
            update(MediaFile)
            .where(
                MediaFile.library_id == library_id,
                MediaFile.last_scan_id != last_scan_id,
                MediaFile.is_orphaned.is_(False),
            )
            .values(is_orphaned=True)
        )
        return int(result.rowcount or 0)

    async def list_paths_for_library(
        self, library_id: str
    ) -> Sequence[str]:
        result = await self._session.execute(
            select(MediaFile.path).where(MediaFile.library_id == library_id)
        )
        return result.scalars().all()

    async def get_tags_for_file(self, media_id: str) -> list[MediaTag]:
        """Return every ``MediaTag`` row for ``media_id``.

        Stage 13 (audit follow-up): used by the dedicated
        ``GET /media/{id}/tags`` endpoint. Order is ``(source, name)``
        so the drawer can render grouped sections without re-sorting.
        Returns an empty list when the file has no tags — the API
        layer is responsible for deciding whether the file itself
        exists (404 vs empty list).
        """
        result = await self._session.execute(
            select(MediaTag)
            .where(MediaTag.media_file_id == media_id)
            .order_by(MediaTag.source, MediaTag.name)
        )
        return list(result.scalars().all())
