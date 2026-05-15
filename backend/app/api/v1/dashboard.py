"""Dashboard router (``/api/v1/dashboard``)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.auth_deps import CurrentUser
from app.api.dependencies import SessionDep, SettingsDep
from app.schemas.dashboard import (
    CategoryBreakdownRead,
    DashboardOverviewRead,
    DashboardSeriesRead,
    IntegrationHealthRead,
    LibrarySeverityRead,
    RecentJobRunRead,
    RecentScanRead,
    SidebarBadgesRead,
    TopRuleRead,
)
from app.services.dashboard import DashboardStats
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
    limit: int = Query(default=12, ge=1, le=50),
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
