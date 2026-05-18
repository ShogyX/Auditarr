"""Dashboard router (``/api/v1/dashboard``)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.auth_deps import CurrentUser
from app.api.dependencies import SessionDep, SettingsDep
from app.schemas.dashboard import (
    CategoryBreakdownRead,
    CompositionRead,
    DashboardOverviewRead,
    DashboardSeriesRead,
    ForeignAudioSummaryRead,
    IncompatibleMediaSummaryRead,
    IntegrationHealthRead,
    LibrarySeverityRead,
    RecentJobRunRead,
    RecentScanRead,
    SidebarBadgesRead,
    TopRuleRead,
)
from app.services.dashboard import DashboardStats
from app.services.dashboard.composition import LibraryCompositionService
from app.services.dashboard.stats import resolve_issue_min_severity_rank

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _issue_min_rank(settings) -> int:
    """Resolve the configured issues-open threshold label to a rank.

    Stage 4 (audit follow-up): the dashboard tile and sidebar badge
    now honour ``dashboard_issue_min_severity``. Read at request time
    so a runtime-settings override applied via ``PUT /system/runtime-settings``
    takes effect immediately (no service restart required).
    """
    return resolve_issue_min_severity_rank(
        getattr(settings, "dashboard_issue_min_severity", "warn")
    )


@router.get(
    "/overview",
    response_model=DashboardOverviewRead,
    summary="Headline numbers for the dashboard",
)
async def overview(
    _user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> DashboardOverviewRead:
    data = await DashboardStats(
        session, issue_min_severity_rank=_issue_min_rank(settings)
    ).overview()
    return DashboardOverviewRead.model_validate(data)


@router.get(
    "/series",
    response_model=DashboardSeriesRead,
    summary="Daily rollups for sparkline charts (Stage 14.1)",
)
async def series(
    _user: CurrentUser,
    session: SessionDep,
    days: int = Query(30, ge=1, le=90),
) -> DashboardSeriesRead:
    data = await DashboardStats(session).series(days=days)
    return DashboardSeriesRead.model_validate(data)


@router.get(
    "/libraries",
    response_model=list[LibrarySeverityRead],
    summary="Per-library severity breakdown",
)
async def libraries(
    _user: CurrentUser, session: SessionDep
) -> list[LibrarySeverityRead]:
    rows = await DashboardStats(session).library_severity()
    return [LibrarySeverityRead.model_validate(row) for row in rows]


@router.get(
    "/integrations",
    response_model=list[IntegrationHealthRead],
    summary="Integration health snapshot",
)
async def integrations(
    _user: CurrentUser, session: SessionDep
) -> list[IntegrationHealthRead]:
    rows = await DashboardStats(session).integration_health()
    return [IntegrationHealthRead.model_validate(row) for row in rows]


@router.get(
    "/top-rules",
    response_model=list[TopRuleRead],
    summary="Rules ranked by current match count",
)
async def top_rules(
    _user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=5, ge=1, le=50),
) -> list[TopRuleRead]:
    rows = await DashboardStats(session).top_rules(limit=limit)
    return [TopRuleRead.model_validate(row) for row in rows]


@router.get(
    "/recent-scans",
    response_model=list[RecentScanRead],
    summary="Recent scan runs",
)
async def recent_scans(
    _user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[RecentScanRead]:
    rows = await DashboardStats(session).recent_scans(limit=limit)
    return [RecentScanRead.model_validate(row) for row in rows]


@router.get(
    "/recent-job-runs",
    response_model=list[RecentJobRunRead],
    summary="Recent automation job runs",
)
async def recent_job_runs(
    _user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[RecentJobRunRead]:
    rows = await DashboardStats(session).recent_job_runs(limit=limit)
    return [RecentJobRunRead.model_validate(row) for row in rows]


@router.get(
    "/sidebar-badges",
    response_model=SidebarBadgesRead,
    summary="Counters for the sidebar navigation badges",
)
async def sidebar_badges(
    _user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> SidebarBadgesRead:
    badges = await DashboardStats(
        session, issue_min_severity_rank=_issue_min_rank(settings)
    ).sidebar_badges()
    return SidebarBadgesRead(**badges)


# ── Stage 26: library composition ─────────────────────────────
@router.get(
    "/categories",
    response_model=list[CategoryBreakdownRead],
    summary="Library composition grouped by codec / container (Stage 26)",
)
async def categories(
    _user: CurrentUser,
    session: SessionDep,
    # v1.9 audit fix (LOG-AUDIT-1): the upper bound used to be
    # 50, but ``CodecFilterMenu`` legitimately fetches up to 64
    # to cover libraries with many distinct codecs/containers.
    # Every files-page render hit a 422 before this raise. We
    # cap at 128 — generous enough for any realistic library
    # without unbounded growth.
    limit: int = Query(default=12, ge=1, le=128),
) -> list[CategoryBreakdownRead]:
    """Returns up to ``limit`` rows per group, ordered by total size
    descending. The response interleaves the ``video_codec`` group
    and the ``container`` group; the UI partitions them by the
    ``group`` discriminator.

    Real data — sourced from probed ``video_codec`` and
    ``container`` columns on ``media_files`` that ffprobe populates
    during a scan. Files the scanner couldn't probe are collapsed
    into a single ``unknown`` row per group rather than padding the
    response with NULL entries — a non-trivial ``unknown`` count is
    a useful signal that the probe stage is failing on some files.
    """
    rows = await DashboardStats(session).categories(limit=limit)
    return [CategoryBreakdownRead.model_validate(row) for row in rows]


# ── v1.9 Stage 3.3 — Library composition ───────────────────────


@router.get(
    "/composition",
    response_model=CompositionRead,
    summary="Library composition payload for the redesigned Categories card",
)
async def composition(
    _user: CurrentUser,
    session: SessionDep,
    library_id: str | None = Query(default=None),
) -> CompositionRead:
    """Build the full composition payload in one call.

    v1.9 Stage 3.3 — replaces the bar-graph Categories card with a
    structured panel: resolutions, top extensions, normalized
    container labels, subtitle formats and languages, audio
    languages, unknown-track counts, internal-vs-external subtitle
    split, orphan count, and a per-cell median-bitrate matrix.

    All aggregations are scoped to ``category == 'media'`` rows
    (v1.9 Stage 3.5) — sidecar files (.nfo, .jpg, .srt) never
    inflate the counts. The external-subtitle row of the
    internal/external section is the one exception: it joins on
    ``category == 'subtitle'`` so the operator can compare
    embedded-stream coverage vs sidecar coverage on the same card.

    ``library_id`` scopes every section. Omit it to get a
    library-agnostic view across the whole install — useful for
    the dashboard's default state before the operator picks a
    library.
    """
    payload = await LibraryCompositionService(session).build(
        library_id=library_id
    )
    return CompositionRead.model_validate(payload)


# ── v1.9 Stage 9.5.7 (OP-8 / OP-9) ──────────────────────────────


@router.get(
    "/foreign-audio",
    response_model=ForeignAudioSummaryRead,
    summary="Files with non-preferred audio and no preferred subtitles",
)
async def foreign_audio(
    _user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ForeignAudioSummaryRead:
    """v1.9 Stage 9.5.7 (OP-8) — dashboard surface for media
    whose primary audio is in a non-preferred language AND that
    carries no subtitle track in any preferred language.

    The preferences come from
    ``settings.preferred_audio_languages`` and
    ``settings.preferred_subtitle_languages`` (operator-editable
    in Settings → Workspace). When both lists are empty the
    surface reports zero — the operator hasn't asked for any
    filtering, so there's nothing to flag.
    """
    from app.services.dashboard.foreign_audio import (
        ForeignAudioService,
    )

    svc = ForeignAudioService(session=session, settings=settings)
    return await svc.summary()


@router.get(
    "/incompatible-media",
    response_model=IncompatibleMediaSummaryRead,
    summary="Files flagged by rules carrying incompatible-audio/video actions",
)
async def incompatible_media(
    _user: CurrentUser,
    session: SessionDep,
) -> IncompatibleMediaSummaryRead:
    """v1.9 Stage 9.5.7 (OP-9) — dashboard surface for media
    flagged by operator-authored rules whose action set carries
    ``incompatible_audio`` or ``incompatible_video`` semantics
    (via add_tag actions whose tag matches the conventional
    ``incompatible-audio`` / ``incompatible-video`` names).

    The matching rules are configured by the operator via the
    existing rule editor; this surface just counts files where
    any such rule fired. Zero counts when no such rule exists,
    or when no rule has matched any file yet.
    """
    from app.services.dashboard.incompatible_media import (
        IncompatibleMediaService,
    )

    return await IncompatibleMediaService(session=session).summary()
