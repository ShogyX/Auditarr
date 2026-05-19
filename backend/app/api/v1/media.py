"""Media router (``/api/v1/media``)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep, SettingsDep
from app.core.exceptions import NotFoundError, ValidationError
from app.models.media import MediaFile
from app.schemas.media import (
    MatchedRuleSummary as SchemaMatchedRuleSummary,
    MediaFileDetail,
    MediaFileSummary,
    MediaPageRead,
    MediaTagRead,
)
from app.schemas.rules import RuleEvaluationRead
from app.services.media import Scanner, get_ffprobe_service
from app.services.media_delete_service import MediaDeleteService
from app.services.repositories import (
    MediaFilter,
    MediaRepository,
    RuleEvaluationRepository,
    RuleRepository,
)
from app.services.repositories.media import SORTABLE_COLUMNS
from app.services.rules_service import RulesService

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=MediaPageRead, summary="List media files")
async def list_media(
    _user: CurrentUser,
    session: SessionDep,
    library_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    extension: str | None = Query(default=None),
    is_orphaned: bool | None = Query(default=None),
    # Stage 27 had ``quarantined`` and ``include_quarantined`` query
    # params here. Stage 05 (v1.7) removed both alongside the
    # quarantine workflow they served (Section A.0 — "delete means
    # delete"). Callers that used to pass ``quarantined=false``
    # for the default Files view need no change — every row is
    # implicitly "not quarantined" now.
    # Stage 31: codec + container filters. Comma-separated to
    # match the existing severity-filter convention (one query
    # param, multi-value supported). The UI populates these from
    # the values it sees on the dashboard `/categories` endpoint,
    # so callers should expect values like "hevc,h264" or
    # "matroska,mp4".
    video_codec: str | None = Query(default=None, max_length=512),
    container: str | None = Query(default=None, max_length=512),
    # Stage 3 (audit follow-up): scope tri-state. Independent of
    # ``category`` (which still does an exact equality filter when
    # set). ``scope=media`` selects rows where category=="media";
    # ``scope=non-media`` selects everything else; ``scope=all``
    # or absent does no scope filtering.
    scope: str | None = Query(
        default=None,
        pattern=r"^(all|media|non-media)$",
        description=(
            "Scope filter: 'media' → only category=media, "
            "'non-media' → only category!=media, 'all' or absent → no filter."
        ),
    ),
    # Stage 3 (audit follow-up): empty-severity-filter sentinel.
    # The frontend lets operators toggle every severity chip off.
    # ``severities_empty=true`` tells the server "the operator
    # actively means zero severities" — the response is then a
    # zero-row page. Distinct from omitting ``severity`` entirely
    # (which means "no severity filter, return all").
    severities_empty: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=512),
    sort: str | None = Query(
        default=None,
        description=(
            "Column to sort by. One of: "
            + ", ".join(SORTABLE_COLUMNS)
            + ". Unknown values fall back to severity-first ordering."
        ),
    ),
    sort_dir: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    # Stage 3 (audit follow-up): toggle the matched-rules join. Off
    # by default so dashboard-style callers don't pay for the
    # second query; the Files page enables it for the chip-strip
    # column.
    include_matched_rules: bool = Query(default=False),
    # Stage 13 (audit follow-up): toggle the tags join. Off by
    # default. The Files page enables it when the optional "tags"
    # column is on.
    include_tags: bool = Query(default=False),
    # Stage 02 (v1.7) — per-column quick filters. These mirror the
    # ``MediaFilter`` fields added in the same stage; the router
    # is a thin pass-through. See the dataclass for the contract
    # of each parameter.
    path_contains: str | None = Query(default=None, max_length=512),
    codec_contains: str | None = Query(default=None, max_length=64),
    container_eq: str | None = Query(default=None, max_length=64),
    extension_eq: str | None = Query(default=None, max_length=16),
    size_min: int | None = Query(default=None, ge=0),
    size_max: int | None = Query(default=None, ge=0),
    mtime_after: str | None = Query(
        default=None,
        description="ISO 8601 timestamp. Files with mtime >= this are returned.",
    ),
    mtime_before: str | None = Query(
        default=None,
        description="ISO 8601 timestamp. Files with mtime <= this are returned.",
    ),
    # v1.10 — tag include/exclude filters. ``tags_any`` already
    # exists from Stage 18; the new pair extends with AND semantics
    # and an exclusion list. Comma-separated to match the codec /
    # container precedent.
    tags_any: str | None = Query(default=None, max_length=1024),
    tags_all: str | None = Query(default=None, max_length=1024),
    tags_none: str | None = Query(default=None, max_length=1024),
    # v1.10 — rule include/exclude filters. Values are rule IDs
    # (UUIDs); the UI looks them up from /rules so the URL stays
    # stable across rule renames.
    rules_any: str | None = Query(default=None, max_length=2048),
    rules_all: str | None = Query(default=None, max_length=2048),
    rules_none: str | None = Query(default=None, max_length=2048),
    has_subtitles: bool | None = Query(default=None),
    resolution_bucket: str | None = Query(
        default=None,
        pattern=r"^(sd|480p|720p|1080p|1440p|2160p|4k|4320p|8k|unknown)$",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> MediaPageRead:
    # Stage 02 — parse the ISO mtime filters once, here, so the
    # repository can be tested with concrete datetimes rather than
    # strings. Reject malformed values with a 422 (FastAPI's
    # ValidationError → standard 422 response).
    from datetime import datetime as _dt

    def _parse_iso(value: str | None, *, field_name: str) -> _dt | None:
        if value is None or value == "":
            return None
        try:
            # ``fromisoformat`` accepts both naive (no tz) and
            # offset-bearing timestamps. The caller is responsible
            # for sending consistent values; we don't silently
            # rebase to UTC.
            return _dt.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(
                f"{field_name} must be an ISO 8601 timestamp"
            ) from exc

    parsed_mtime_after = _parse_iso(mtime_after, field_name="mtime_after")
    parsed_mtime_before = _parse_iso(mtime_before, field_name="mtime_before")

    def _split_csv(value: str | None) -> list[str] | None:
        if value is None:
            return None
        items = [piece.strip() for piece in value.split(",") if piece.strip()]
        return items or None

    page = await MediaRepository(session).list(
        filt=MediaFilter(
            library_id=library_id,
            category=category,
            severity=severity,
            extension=extension,
            is_orphaned=is_orphaned,
            video_codec=video_codec,
            container=container,
            search=search,
            sort=sort,
            sort_dir=sort_dir,
            scope=scope,  # type: ignore[arg-type]
            severities_empty=severities_empty,
            include_matched_rules=include_matched_rules,
            include_tags=include_tags,
            # Stage 02 — per-column filter pass-through.
            path_contains=path_contains,
            codec_contains=codec_contains,
            container_eq=container_eq,
            extension_eq=extension_eq,
            size_min=size_min,
            size_max=size_max,
            mtime_after=parsed_mtime_after,
            mtime_before=parsed_mtime_before,
            # v1.10 — tag and rule include/exclude.
            tags_any=_split_csv(tags_any),
            tags_all=_split_csv(tags_all),
            tags_none=_split_csv(tags_none),
            rules_any=_split_csv(rules_any),
            rules_all=_split_csv(rules_all),
            rules_none=_split_csv(rules_none),
            has_subtitles=has_subtitles,
            resolution_bucket=resolution_bucket,
        ),
        offset=offset,
        limit=limit,
    )

    # Stage 3 (audit follow-up): if the matched-rules join ran,
    # decorate each row's serialized summary with its rule chips.
    # We round-trip through model_validate so the attribute-mapping
    # picks up every column on MediaFile, then patch in the joined
    # data. Cheaper than threading a dataclass union through the
    # serializer.
    items: list[MediaFileSummary] = []
    for m in page.items:
        summary = MediaFileSummary.model_validate(m)
        update_dict: dict[str, object] = {}
        if include_matched_rules:
            joined = page.matched_rules.get(m.id, [])
            update_dict["matched_rules"] = [
                SchemaMatchedRuleSummary(
                    rule_id=j.rule_id,
                    rule_name=j.rule_name,
                    severity=j.severity,
                )
                for j in joined
            ]
        # Stage 13 (audit follow-up): patch in the tag-name list when
        # the join ran.
        if include_tags:
            update_dict["tags"] = page.tags.get(m.id, [])
        if update_dict:
            summary = summary.model_copy(update=update_dict)
        items.append(summary)

    return MediaPageRead(
        items=items,
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


# ── Stage 15: context-driven dropdowns vocabulary endpoint ──────


class MediaVocabulary(BaseModel):
    """Distinct values currently in the indexed library.

    Used by the rule builder, optimization profile dialog, and
    automation schedule editor to present operator-typeable
    fields as multi-selects driven by what the scanner has
    actually seen — instead of free-text inputs that risk
    typos.

    All five columns mirror the matching fields on
    ``MediaFile`` / ``MediaTag``. Empty strings and NULLs are
    excluded; values come back sorted asc for stable UI
    rendering.
    """

    model_config = ConfigDict(extra="forbid")

    video_codecs: list[str] = Field(default_factory=list)
    audio_codecs: list[str] = Field(default_factory=list)
    containers: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# Module-level TTL cache. Plan §656 specifies 60s. A library
# of any size produces tiny result sets (the cardinality of
# distinct codecs / containers / extensions is bounded by the
# real-world list of formats, NOT by file count), so the cache
# is purely a request-storm dampener — when the rules page
# opens and the operator picks five different fields in a row,
# we'd otherwise hit the database five times for the same
# result.
_VOCABULARY_CACHE_TTL_SECONDS = 60.0
_vocabulary_cache: tuple[float, MediaVocabulary] | None = None


def _vocabulary_cache_clear() -> None:
    """Test hook — clear the in-process TTL cache so tests
    don't leak vocabularies into each other."""
    global _vocabulary_cache
    _vocabulary_cache = None


@router.get(
    "/vocabulary",
    response_model=MediaVocabulary,
    summary="Distinct codec / container / extension / tag values in the library",
)
async def get_media_vocabulary(
    _user: CurrentUser,
    session: SessionDep,
) -> MediaVocabulary:
    """Returns the distinct values currently in the library.

    Cached in-process for 60 seconds. The cache key is
    library-global (no per-user variance), so cross-user cache
    hits are correct.

    Stage 15 (plan §656) — frontend rule / profile / automation
    surfaces consume this to drive value-pickers from the
    library's actual content rather than free-text.
    """
    import time as _time

    from sqlalchemy import select

    from app.models.media import MediaFile
    from app.models.tag import MediaTag

    global _vocabulary_cache

    now = _time.monotonic()
    if _vocabulary_cache is not None:
        ts, payload = _vocabulary_cache
        if now - ts < _VOCABULARY_CACHE_TTL_SECONDS:
            return payload

    async def _distinct(column) -> list[str]:
        # SELECT DISTINCT <column> excluding NULL and empty
        # string. Sort ascending for stable UI rendering.
        stmt = (
            select(column)
            .where(column.is_not(None))
            .where(column != "")
            .distinct()
            .order_by(column.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [r for r in rows if r]

    video_codecs = await _distinct(MediaFile.video_codec)
    audio_codecs = await _distinct(MediaFile.audio_codec)
    containers = await _distinct(MediaFile.container)
    extensions = await _distinct(MediaFile.extension)
    tags = await _distinct(MediaTag.name)

    payload = MediaVocabulary(
        video_codecs=video_codecs,
        audio_codecs=audio_codecs,
        containers=containers,
        extensions=extensions,
        tags=tags,
    )
    _vocabulary_cache = (now, payload)
    return payload


# ── v1.9 Stage 3.1 — Distinct values for per-column filters ───


# Whitelist of columns the operator can ask for distinct values
# of. Anything outside this list is rejected as a 422. Two reasons
# we whitelist rather than allow any column:
#
#   * Free-form column names would let a caller probe internal
#     columns (``hash_sha256``, ``probe`` JSON blob) that aren't
#     meant to surface in a filter popover.
#   * Several columns store JSON lists (``subtitle_languages``,
#     ``audio_languages``) and need a different aggregation path
#     than scalar columns; routing inside this set makes that
#     explicit rather than a runtime branch on type.
#
# The frontend ColumnFilterPopover passes the column key from the
# table header; both sides MUST agree on the set.
_DISTINCT_SCALAR_FIELDS: dict[str, object] = {
    "severity": MediaFile.severity,
    "category": MediaFile.category,
    "extension": MediaFile.extension,
    "video_codec": MediaFile.video_codec,
    "audio_codec": MediaFile.audio_codec,
    "subtitle_codec": MediaFile.subtitle_codec,
    "container": MediaFile.container,
    "library_id": MediaFile.library_id,
    "width": MediaFile.width,
    "height": MediaFile.height,
    # ``framerate`` is a float; aggregating distinct floats is
    # noisy. The popover's UX (a single "30 / 60 / 23.976 / …"
    # picklist) still benefits from grouping, so we expose it
    # here. Buckets are the consumer's responsibility.
    "framerate": MediaFile.framerate,
}

_DISTINCT_JSON_LIST_FIELDS: dict[str, object] = {
    # JSON-list columns — aggregated in Python like the
    # composition service does for language counts.
    "subtitle_languages": MediaFile.subtitle_languages,
    "audio_languages": MediaFile.audio_languages,
}

DISTINCT_FIELDS: frozenset[str] = frozenset(
    {*_DISTINCT_SCALAR_FIELDS.keys(), *_DISTINCT_JSON_LIST_FIELDS.keys()}
)


class DistinctValueRead(BaseModel):
    """One row in the distinct-values list."""

    value: str | None
    """``None`` represents the NULL bucket (files with no value
    in this column). The UI renders NULL as "(none)"."""

    count: int


class DistinctValuesResponse(BaseModel):
    field: str
    values: list[DistinctValueRead]
    """At most 200 rows, sorted by descending count then ascending
    value. The cap keeps the popover responsive; if the operator
    needs to find a value not in the top 200, the ``prefix`` query
    param narrows the result set first."""

    truncated: bool
    """True if there were more distinct values than the 200-row
    cap. The UI can surface a "more results — narrow your search"
    hint."""


@router.get(
    "/distinct",
    response_model=DistinctValuesResponse,
    summary="Distinct values + counts for a column (filter popover)",
)
async def media_distinct(
    field: str,
    _user: CurrentUser,
    session: SessionDep,
    library_id: str | None = Query(default=None),
    prefix: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=200, ge=1, le=500),
) -> DistinctValuesResponse:
    """Return the top distinct values for a column, with counts.

    Used by the v1.9 Stage 3.1 ColumnFilterPopover on every
    filterable column header. The popover queries here on open
    and on each search-input keystroke (debounced), so the
    response shape is intentionally compact:

      * ``values`` is up to ``limit`` rows (default 200, capped 500).
      * Each row is just ``{value, count}``. The popover renders
        the value as the checkbox label and the count as a
        muted-text suffix.
      * NULL values surface as ``value=None`` — the popover
        renders them as "(none)" so the operator can filter on
        "files with no codec".
      * ``library_id`` scopes the aggregation; ``prefix`` is a
        case-insensitive prefix match on the column value.

    ``field`` must be in ``DISTINCT_FIELDS`` (the whitelist
    documented inline above). Anything else returns 422.
    """
    if field not in DISTINCT_FIELDS:
        raise ValidationError(
            f"field {field!r} not in distinct whitelist: "
            f"{sorted(DISTINCT_FIELDS)}"
        )

    if field in _DISTINCT_SCALAR_FIELDS:
        column = _DISTINCT_SCALAR_FIELDS[field]
        rows, truncated = await _distinct_scalar(
            session, column, library_id=library_id, prefix=prefix, limit=limit
        )
    else:
        column = _DISTINCT_JSON_LIST_FIELDS[field]
        rows, truncated = await _distinct_json_list(
            session, column, library_id=library_id, prefix=prefix, limit=limit
        )

    return DistinctValuesResponse(
        field=field,
        values=[
            DistinctValueRead(value=v, count=c) for v, c in rows
        ],
        truncated=truncated,
    )


async def _distinct_scalar(
    session,
    column,
    *,
    library_id: str | None,
    prefix: str | None,
    limit: int,
) -> tuple[list[tuple[str | None, int]], bool]:
    """Scalar-column distinct: GROUP BY column, ORDER BY count desc."""
    from sqlalchemy import func, select

    stmt = (
        select(column, func.count(MediaFile.id))
        .where(MediaFile.category == "media")
        .group_by(column)
    )
    if library_id is not None:
        stmt = stmt.where(MediaFile.library_id == library_id)
    if prefix and prefix.strip():
        # Case-insensitive prefix match. Using ``lower(col)``
        # rather than ILIKE keeps the query portable across
        # sqlite + postgres.
        p = prefix.strip().lower()
        stmt = stmt.where(func.lower(column).like(f"{p}%"))
    # Ask for one extra row so we know whether to set ``truncated``.
    stmt = stmt.order_by(func.count(MediaFile.id).desc()).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > limit
    rows = rows[:limit]
    out: list[tuple[str | None, int]] = []
    for value, count in rows:
        if value is None:
            out.append((None, int(count)))
        else:
            out.append((str(value), int(count)))
    return out, truncated


async def _distinct_json_list(
    session,
    column,
    *,
    library_id: str | None,
    prefix: str | None,
    limit: int,
) -> tuple[list[tuple[str | None, int]], bool]:
    """JSON-list distinct: fetch raw lists, aggregate in Python.

    See the composition service's ``_language_counts`` for the
    same pattern. The cardinality is bounded by per-file list
    length (3-5 entries typical) so the streaming cost is fine.
    """
    from sqlalchemy import select

    stmt = select(column).where(
        MediaFile.category == "media", column.isnot(None)
    )
    if library_id is not None:
        stmt = stmt.where(MediaFile.library_id == library_id)
    rows = (await session.execute(stmt)).all()
    counts: dict[str, int] = {}
    p = (prefix.strip().lower() if prefix and prefix.strip() else None)
    for (values,) in rows:
        if not values:
            continue
        for v in values:
            if not isinstance(v, str) or not v:
                continue
            key = v.strip().lower()
            if not key:
                continue
            if p is not None and not key.startswith(p):
                continue
            counts[key] = counts.get(key, 0) + 1
    # Sort by count desc, then value asc for stable ordering.
    sorted_items = sorted(
        counts.items(), key=lambda kv: (-kv[1], kv[0])
    )
    truncated = len(sorted_items) > limit
    return [(k, v) for k, v in sorted_items[:limit]], truncated
@router.get("/{media_id}", response_model=MediaFileDetail, summary="Media file detail")
async def get_media(
    media_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> MediaFileDetail:
    record = await MediaRepository(session).get(media_id)
    if record is None:
        raise NotFoundError("Media file not found")
    return MediaFileDetail.model_validate(record)


# ── Stage 23: per-file evaluations + bulk re-evaluation ──────────
class MediaEvaluationRead(RuleEvaluationRead):
    """Rule evaluation enriched with the rule name + severity label.

    The base ``RuleEvaluationRead`` only carries the rule_id; the Files
    detail drawer wants the human-readable name without a second
    round-trip per rule. Adding the join here keeps the drawer's
    fetch count at exactly one extra request per file.
    """

    rule_name: str
    rule_enabled: bool


@router.get(
    "/{media_id}/evaluations",
    response_model=list[MediaEvaluationRead],
    summary="Rule evaluations for one media file",
)
async def list_media_evaluations(
    media_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> list[MediaEvaluationRead]:
    """Latest evaluation per rule for the given file, severity-ordered.

    The ``rule_evaluations`` table is upserted per (file, rule) pair so
    there's at most one row per rule per file. Rows for disabled or
    deleted rules are still returned — they represent the file's
    historical evaluation state, which is what the detail drawer
    should show.
    """
    if await MediaRepository(session).get(media_id) is None:
        raise NotFoundError("Media file not found")

    eval_repo = RuleEvaluationRepository(session)
    rule_repo = RuleRepository(session)
    evaluations = await eval_repo.list_for_file(media_id)

    # Fetch rules in a single round-trip by collecting the ids first.
    # The set is bounded by the number of enabled rules in the system,
    # typically <100, so a per-id loop is acceptable; we batch anyway
    # to keep the request count tight on large rule sets.
    rule_ids = {ev.rule_id for ev in evaluations}
    rules = {r.id: r for r in await rule_repo.list_all() if r.id in rule_ids}

    out: list[MediaEvaluationRead] = []
    for ev in evaluations:
        rule = rules.get(ev.rule_id)
        out.append(
            MediaEvaluationRead(
                media_file_id=ev.media_file_id,
                rule_id=ev.rule_id,
                severity=ev.severity,
                severity_rank=ev.severity_rank,
                actions_summary=ev.actions_summary,
                evaluated_at=ev.evaluated_at,
                rule_name=rule.name if rule else "(deleted rule)",
                rule_enabled=rule.enabled if rule else False,
            )
        )
    return out


@router.get(
    "/{media_id}/tags",
    response_model=list[MediaTagRead],
    summary="Tags for one media file (Stage 13)",
)
async def list_media_tags(
    media_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> list[MediaTagRead]:
    """All tags for the given file, ordered by ``(source, name)``.

    Stage 13 (audit follow-up): the drawer uses this to render tags
    grouped by their origin (manual / rule / integration). Tag
    casing is preserved exactly as stored — Sonarr's "4K" and a
    hypothetical Radarr "4k" are visibly distinct, which matters
    when auditing why duplicates exist.

    Returns 404 if the file doesn't exist (eviction, deletion) so
    the drawer can fall back to a stale-data error rather than a
    silent empty list.
    """
    repo = MediaRepository(session)
    if await repo.get(media_id) is None:
        raise NotFoundError("Media file not found")
    tags = await repo.get_tags_for_file(media_id)
    return [MediaTagRead.model_validate(t) for t in tags]


# ── Bulk re-evaluation ───────────────────────────────────────────
class BulkReevaluateRequest(BaseModel):
    """Body for ``POST /media/bulk/reevaluate``.

    The cap (``max_length=500``) matches the ``list_media`` page-size
    ceiling; a single bulk request can never select more files than a
    single page could surface. That keeps the worst-case server-side
    load deterministic and avoids letting an over-eager client kick
    off a quasi-library-scale re-evaluation through this endpoint —
    the "evaluate the entire library" path already exists at
    ``POST /api/v1/rules/libraries/{library_id}/evaluate``.
    """

    model_config = ConfigDict(extra="forbid")

    media_ids: list[str] = Field(min_length=1, max_length=500)


class BulkReevaluateResponse(BaseModel):
    files_evaluated: int
    files_not_found: list[str]


@router.post(
    "/bulk/reevaluate",
    response_model=BulkReevaluateResponse,
    summary="Re-evaluate rules against a specific set of files",
)
async def bulk_reevaluate(
    body: BulkReevaluateRequest,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> BulkReevaluateResponse:
    """Run the enabled rule set against the listed files only.

    Use case: the operator narrows the Files table to a problematic
    subset (high severity in one library, say), selects them, and
    asks Auditarr to re-check the rule outcomes — typically after
    editing a rule or restoring an override that affects classification.
    Admin-only; the rules engine writes to ``rule_evaluations`` and
    mutates the file's denormalized ``severity`` / ``severity_rank``
    columns, so it needs the same gate as the per-library
    evaluate endpoint.
    """
    if len(set(body.media_ids)) != len(body.media_ids):
        # We could silently de-duplicate but it's strictly better UX to
        # tell the caller their list was malformed — duplicates almost
        # always signal a bug in whichever code aggregated the
        # selection.
        raise ValidationError("media_ids must not contain duplicates")

    media_repo = MediaRepository(session)
    files = []
    not_found: list[str] = []
    for mid in body.media_ids:
        record = await media_repo.get(mid)
        if record is None:
            not_found.append(mid)
        else:
            files.append(record)

    service = RulesService(session=session, event_bus=bus, registry=registry)
    await service.evaluate_files(files)
    await session.commit()

    return BulkReevaluateResponse(
        files_evaluated=len(files),
        files_not_found=not_found,
    )


# ── Stage 27: per-file re-probe ─────────────────────────────────
#
# NOTE on route ordering. FastAPI matches routes in registration
# order. Because the per-file endpoints carry a path parameter
# (``/{media_id}/reprobe``), if those came first the literal
# ``/bulk/reprobe`` path would be eaten by the path-param route
# with ``media_id="bulk"``. Same hazard as Stage 24's
# ``/rules/bundle/export`` next to ``/rules/{rule_id}`` — solved
# the same way: declare the bulk endpoints (with literal segments)
# BEFORE the parameterized per-file endpoints. The schemas live
# next to their endpoint for readability.


# ── Bulk reprobe ────────────────────────────────────────────────


class BulkReprobeRequest(BaseModel):
    """Body for ``POST /media/bulk/reprobe`` (Stage 27).

    The same 500-item cap as ``BulkReevaluateRequest`` — selection
    size is bounded by the Files page max-page, and the bulk
    endpoint should never invite library-scale rework that the
    full-scan endpoint already handles.
    """

    model_config = ConfigDict(extra="forbid")
    media_ids: list[str] = Field(min_length=1, max_length=500)


class BulkReprobeResponse(BaseModel):
    files_reprobed: int
    files_failed: int
    files_not_found: list[str]
    files_orphaned: int


@router.post(
    "/bulk/reprobe",
    response_model=BulkReprobeResponse,
    summary="Re-run ffprobe on a specific set of files (Stage 27)",
)
async def bulk_reprobe(
    body: BulkReprobeRequest,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> BulkReprobeResponse:
    """Re-probe a list of files sequentially.

    Concurrency is bounded by ``FfprobeService.max_concurrency``
    (currently 4) — the service has its own semaphore, so even
    though we issue probes one-by-one here, the underlying
    ffprobe invocations are serialized inside the service. Doing
    it this way (rather than ``asyncio.gather`` at this layer)
    keeps the SQLAlchemy session usage single-threaded, which
    matches the rest of the codebase.

    Partial failures are reported per-file rather than failing the
    batch. ``files_orphaned`` separates "the file is gone" from
    "ffprobe couldn't read it" — both are real outcomes the
    operator wants to know about.
    """
    if len(set(body.media_ids)) != len(body.media_ids):
        raise ValidationError("media_ids must not contain duplicates")

    repo = MediaRepository(session)
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=get_ffprobe_service(),
        registry=registry,
    )

    reprobed = failed = orphaned = 0
    not_found: list[str] = []
    for mid in body.media_ids:
        record = await repo.get(mid)
        if record is None:
            not_found.append(mid)
            continue
        await scanner.reprobe_one(record)
        if record.is_orphaned:
            orphaned += 1
        elif record.probe_failed:
            failed += 1
        else:
            reprobed += 1
    await session.commit()
    return BulkReprobeResponse(
        files_reprobed=reprobed,
        files_failed=failed,
        files_not_found=not_found,
        files_orphaned=orphaned,
    )


# Stage 27 had ``POST /media/bulk/quarantine`` and
# ``POST /media/bulk/unquarantine`` here, plus their request /
# response models. Stage 05 (v1.7) removed all four
# (Section A.0 — "delete means delete"). Operators who want to
# act on a selection now have two paths:
#
#   * Add a tag via the existing bulk-tag flow + write a rule
#     that matches on the tag.
#   * For destructive intent, run a rule with a Delete action;
#     the audit log records each removal.
#
# The Files-page selection bar's "Quarantine selected" button
# is also removed in the frontend portion of this stage.


# ── Per-file reprobe ────────────────────────────────────────────


@router.post(
    "/{media_id}/reprobe",
    response_model=MediaFileDetail,
    summary="Re-run ffprobe on a single file (Stage 27)",
)
async def reprobe_media(
    media_id: str,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> MediaFileDetail:
    """Refresh probe metadata for a single file without a full scan.

    Use case: the operator notices a file's probe is stale or
    failed during the original scan, and wants to refresh just
    that one entry. Admin-only — the operation mutates probe
    columns and bumps ``seen_at``.

    If the file path is missing on disk, the row is flagged
    ``is_orphaned=True`` and returned as-is (we don't 404 — the
    operator just asked us to check, and "the file is gone" is
    itself the answer they need to see).
    """
    record = await MediaRepository(session).get(media_id)
    if record is None:
        raise NotFoundError("Media file not found")

    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=get_ffprobe_service(),
        registry=registry,
    )
    await scanner.reprobe_one(record)
    await session.commit()
    await session.refresh(record)
    return MediaFileDetail.model_validate(record)


# Stage 27 had per-file ``POST /media/{media_id}/quarantine`` and
# ``POST /media/{media_id}/unquarantine`` endpoints here. Stage 05
# (v1.7) removed both alongside the rest of the quarantine
# workflow (Section A.0 — "delete means delete"). The drawer's
# quarantine button is gone in the frontend portion of this stage.


# ── v1.9 Stage 2.4 — Operator-initiated delete ─────────────────


class DeleteOneRequest(BaseModel):
    """Body for ``DELETE /media/{media_id}``.

    ``remove_from_disk`` defaults to False — index-only delete is
    the safe default; the file on disk is untouched and the next
    scan will re-index it. Set to True to also move the file to
    the trash dir.
    """

    model_config = ConfigDict(extra="forbid")

    remove_from_disk: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class BulkDeleteRequest(BaseModel):
    """Body for ``POST /media/bulk-delete``."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=500)
    remove_from_disk: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class DeleteResultRead(BaseModel):
    """One file's outcome from a delete call."""

    media_id: str
    path: str
    removed_from_disk: bool
    trash_path: str | None


class BulkDeleteResponse(BaseModel):
    deleted: list[DeleteResultRead]
    requested: int
    not_found: list[str]


@router.delete(
    "/{media_id}",
    response_model=DeleteResultRead,
    summary="Delete a single media file (admin)",
)
async def delete_media(
    media_id: str,
    body: DeleteOneRequest,
    user: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> DeleteResultRead:
    """Delete one media file's index row, optionally also moving the
    on-disk file to ``data_dir/trash/<yyyy-mm-dd>/<uuid>/...``.

    The audit log captures who triggered the delete, when, the
    original path, and the trash path (if any). 404 if the row
    doesn't exist.
    """
    service = MediaDeleteService(
        session=session, settings=settings, event_bus=bus
    )
    try:
        result = await service.delete_one(
            media_id,
            actor_id=user.id,
            remove_from_disk=body.remove_from_disk,
            reason=body.reason,
        )
    except LookupError as exc:
        raise NotFoundError(f"Media file {media_id!r} not found") from exc
    await session.commit()
    return DeleteResultRead(
        media_id=result.media_id,
        path=result.path,
        removed_from_disk=result.removed_from_disk,
        trash_path=result.trash_path,
    )


@router.post(
    "/bulk-delete",
    response_model=BulkDeleteResponse,
    summary="Delete multiple media files in one call (admin)",
)
async def bulk_delete_media(
    body: BulkDeleteRequest,
    user: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> BulkDeleteResponse:
    """Bulk variant of ``DELETE /media/{id}``.

    All files in a single bulk call share one trash bucket when
    ``remove_from_disk=true``, so the operator can recover the
    whole batch by moving one directory. Unknown ids are silently
    skipped and reported in ``not_found``; the call doesn't fail
    just because the operator selected a stale row that another
    process already cleaned up.
    """
    if len(set(body.ids)) != len(body.ids):
        # Duplicates in the list almost always indicate a client
        # bug aggregating the selection; surface it loudly.
        raise ValidationError("ids must not contain duplicates")

    service = MediaDeleteService(
        session=session, settings=settings, event_bus=bus
    )
    results = await service.bulk_delete(
        body.ids,
        actor_id=user.id,
        remove_from_disk=body.remove_from_disk,
        reason=body.reason,
    )
    await session.commit()
    deleted_ids = {r.media_id for r in results}
    not_found = [mid for mid in body.ids if mid not in deleted_ids]
    return BulkDeleteResponse(
        deleted=[
            DeleteResultRead(
                media_id=r.media_id,
                path=r.path,
                removed_from_disk=r.removed_from_disk,
                trash_path=r.trash_path,
            )
            for r in results
        ],
        requested=len(body.ids),
        not_found=not_found,
    )
