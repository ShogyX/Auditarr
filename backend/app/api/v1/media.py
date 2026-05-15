"""Media router (``/api/v1/media``)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.media import (
    MatchedRuleSummary as SchemaMatchedRuleSummary,
    MediaFileDetail,
    MediaFileSummary,
    MediaPageRead,
    MediaTagRead,
)
from app.schemas.rules import RuleEvaluationRead
from app.services.media import Scanner, get_ffprobe_service
from app.services.repositories import (
    MediaFilter,
    MediaRepository,
    RuleEvaluationRepository,
    RuleRepository,
)
from app.services.repositories.media import SORTABLE_COLUMNS
from app.services.rules_service import RulesService
from app.utils.datetime import utcnow

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
    # Stage 27: quarantine filter.
    # ``None`` (default) → exclude quarantined files. This matches
    # the convention used everywhere else: quarantined files are
    # out-of-scope unless the operator explicitly asks for them.
    # Pass ``quarantined=true`` to surface the quarantine view, or
    # ``include_quarantined=true`` to mix them with regular files
    # (useful for "show everything regardless").
    quarantined: bool | None = Query(default=None),
    include_quarantined: bool = Query(default=False),
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
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> MediaPageRead:
    # Resolve the effective quarantine filter:
    # - explicit `quarantined=true` or `=false` → that exact filter
    # - `include_quarantined=true` → no quarantine filter (return both)
    # - neither set → default to `quarantined=false` (exclude them)
    if quarantined is not None:
        effective_q: bool | None = quarantined
    elif include_quarantined:
        effective_q = None
    else:
        effective_q = False

    page = await MediaRepository(session).list(
        filt=MediaFilter(
            library_id=library_id,
            category=category,
            severity=severity,
            extension=extension,
            is_orphaned=is_orphaned,
            quarantined=effective_q,
            video_codec=video_codec,
            container=container,
            search=search,
            sort=sort,
            sort_dir=sort_dir,
            scope=scope,  # type: ignore[arg-type]
            severities_empty=severities_empty,
            include_matched_rules=include_matched_rules,
            include_tags=include_tags,
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


# ── Stage 27: per-file re-probe + quarantine ────────────────────
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


# ── Bulk reprobe / quarantine / unquarantine ────────────────────


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


class BulkQuarantineRequest(BaseModel):
    """Body for ``POST /media/bulk/quarantine`` (Stage 27)."""

    model_config = ConfigDict(extra="forbid")
    media_ids: list[str] = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=512)


class BulkQuarantineResponse(BaseModel):
    files_quarantined: int
    files_not_found: list[str]


@router.post(
    "/bulk/quarantine",
    response_model=BulkQuarantineResponse,
    summary="Quarantine a specific set of files (Stage 27)",
)
async def bulk_quarantine(
    body: BulkQuarantineRequest,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> BulkQuarantineResponse:
    if len(set(body.media_ids)) != len(body.media_ids):
        raise ValidationError("media_ids must not contain duplicates")
    repo = MediaRepository(session)
    now = utcnow()
    quarantined = 0
    not_found: list[str] = []
    for mid in body.media_ids:
        record = await repo.get(mid)
        if record is None:
            not_found.append(mid)
            continue
        record.quarantined = True
        record.quarantined_at = now
        record.quarantined_reason = body.reason
        quarantined += 1
    await session.commit()
    if quarantined > 0:
        await bus.emit(
            "media.quarantined_bulk",
            {"count": quarantined, "reason": body.reason},
            source="media-api",
        )
    return BulkQuarantineResponse(
        files_quarantined=quarantined,
        files_not_found=not_found,
    )


class BulkUnquarantineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_ids: list[str] = Field(min_length=1, max_length=500)


class BulkUnquarantineResponse(BaseModel):
    files_unquarantined: int
    files_not_found: list[str]


@router.post(
    "/bulk/unquarantine",
    response_model=BulkUnquarantineResponse,
    summary="Restore a specific set of quarantined files (Stage 27)",
)
async def bulk_unquarantine(
    body: BulkUnquarantineRequest,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> BulkUnquarantineResponse:
    if len(set(body.media_ids)) != len(body.media_ids):
        raise ValidationError("media_ids must not contain duplicates")
    repo = MediaRepository(session)
    unquarantined = 0
    not_found: list[str] = []
    for mid in body.media_ids:
        record = await repo.get(mid)
        if record is None:
            not_found.append(mid)
            continue
        if record.quarantined:
            record.quarantined = False
            record.quarantined_at = None
            record.quarantined_reason = None
            unquarantined += 1
    await session.commit()
    if unquarantined > 0:
        await bus.emit(
            "media.unquarantined_bulk",
            {"count": unquarantined},
            source="media-api",
        )
    return BulkUnquarantineResponse(
        files_unquarantined=unquarantined,
        files_not_found=not_found,
    )


# ── Per-file reprobe / quarantine / unquarantine ────────────────


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


class QuarantineRequest(BaseModel):
    """Optional reason on quarantine. Capped at 512 chars to match
    the column."""

    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=512)


@router.post(
    "/{media_id}/quarantine",
    response_model=MediaFileDetail,
    summary="Mark a single file as quarantined (Stage 27)",
)
async def quarantine_media(
    media_id: str,
    body: QuarantineRequest,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> MediaFileDetail:
    """Quarantine a single file.

    Quarantining is idempotent at the row level — quarantining a
    file that's already quarantined refreshes ``quarantined_at``
    and replaces the reason, but doesn't error. (Re-quarantining
    is sometimes a useful "I confirmed this is still broken"
    signal; we don't want to make the operator dig out an old
    error to do it.)
    """
    record = await MediaRepository(session).get(media_id)
    if record is None:
        raise NotFoundError("Media file not found")
    record.quarantined = True
    record.quarantined_at = utcnow()
    record.quarantined_reason = body.reason
    await session.commit()
    await session.refresh(record)
    await bus.emit(
        "media.quarantined",
        {"id": record.id, "reason": body.reason},
        source="media-api",
    )
    return MediaFileDetail.model_validate(record)


@router.post(
    "/{media_id}/unquarantine",
    response_model=MediaFileDetail,
    summary="Restore a quarantined file (Stage 27)",
)
async def unquarantine_media(
    media_id: str,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> MediaFileDetail:
    """Restore a quarantined file to normal operation.

    Idempotent in the same sense as quarantine — unquarantining a
    file that isn't quarantined is a no-op (no error, no event).
    This keeps bulk operations clean: the operator selects a
    range of files and clicks Unquarantine; partial selections
    don't bail.
    """
    record = await MediaRepository(session).get(media_id)
    if record is None:
        raise NotFoundError("Media file not found")
    if record.quarantined:
        record.quarantined = False
        record.quarantined_at = None
        record.quarantined_reason = None
        await session.commit()
        await session.refresh(record)
        await bus.emit(
            "media.unquarantined",
            {"id": record.id},
            source="media-api",
        )
    return MediaFileDetail.model_validate(record)
