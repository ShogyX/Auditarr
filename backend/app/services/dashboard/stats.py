"""Dashboard stats service.

A read-only collection of SQL aggregations over the existing tables.
Each method returns a structured dataclass that the API layer maps to a
Pydantic schema — keeping the SQL out of the API surface so the queries
can grow without churning the public contract.

Stage 8 deliberately ships SQL-driven views rather than a denormalized
``dashboard_stats`` table. The data volumes for a single self-hosted
instance are small enough (~thousands of files, ~tens of rules, ~hundreds
of runs/day) that direct queries are fast, and avoiding a materialized
table means we don't have a cache-invalidation problem. Stage 13 may
revisit this if real-world deployments need it.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration
from app.models.job_run import JobRun
from app.models.library import Library
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.scan_run import ScanRun
from app.rules.schema import SEVERITY_LEVELS
from app.utils.datetime import utcnow


# Stage 4 (audit follow-up): map the configured threshold label to
# its underlying numeric rank. ``warn`` (default) → 40, which
# excludes ok (10) and info (20). Unknown labels fall back to
# ``warn`` rather than 422-ing the API request — the runtime
# settings schema validates the label on write, so an unknown
# value only reaches this resolver under upgrade/downgrade
# scenarios where an older row outlives a schema constraint
# change.
def resolve_issue_min_severity_rank(label: str) -> int:
    """Return the numeric rank a file's ``severity_rank`` must
    meet or exceed to count as an "open issue".

    The resolver is permissive — an unknown label collapses to
    the default ``warn`` so dashboard counts never break on a
    stale value. The runtime-settings write path is strict and
    rejects unknown labels at the API.
    """
    rank = SEVERITY_LEVELS.get(label.lower()) if isinstance(label, str) else None
    if rank is None:
        return SEVERITY_LEVELS["warn"]
    return rank


@dataclass(slots=True)
class SeverityCounts:
    """How many files sit at each severity level."""

    ok: int = 0
    info: int = 0
    warn: int = 0
    high: int = 0
    error: int = 0
    crit: int = 0
    total: int = 0


@dataclass(slots=True)
class LibrarySeverity:
    library_id: str
    library_name: str
    file_count: int
    severity: SeverityCounts


@dataclass(slots=True)
class IntegrationHealth:
    integration_id: str
    name: str
    kind: str
    enabled: bool
    health_status: str
    health_detail: str | None
    health_checked_at: _dt.datetime | None


@dataclass(slots=True)
class TopRule:
    rule_id: str
    name: str
    enabled: bool
    match_count: int


@dataclass(slots=True)
class RecentScan:
    id: str
    library_id: str
    library_name: str
    mode: str
    status: str
    files_seen: int
    started_at: _dt.datetime | None
    finished_at: _dt.datetime | None


@dataclass(slots=True)
class RecentJobRun:
    id: str
    job_kind: str
    status: str
    trigger: str
    started_at: _dt.datetime
    duration_ms: int | None
    error: str | None


@dataclass(slots=True)
class OptimizationCounts:
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0


@dataclass(slots=True)
class DashboardOverview:
    """The headline numbers shown at the top of the dashboard."""

    file_count: int = 0
    library_count: int = 0
    integration_count: int = 0
    integration_ok_count: int = 0
    rule_count: int = 0
    rule_enabled_count: int = 0
    severity_counts: SeverityCounts = field(default_factory=SeverityCounts)
    issues_open: int = 0  # files with severity_rank > 10 (anything past 'ok')
    optimization_counts: OptimizationCounts = field(
        default_factory=OptimizationCounts
    )
    last_scan_at: _dt.datetime | None = None
    # Stage 14.1: surface total library bytes so the dashboard can
    # render the proposed "Library size" tile.
    total_size_bytes: int = 0


@dataclass(slots=True)
class CategoryBreakdown:
    """Stage 26: media-library composition by codec / container.

    Used to render the "Categories" card on the dashboard. The
    ``group`` field discriminates which dimension the row belongs
    to (e.g. ``video_codec`` rows are sectioned separately from
    ``container`` rows in the UI).
    """

    key: str
    label: str
    group: str
    file_count: int
    total_size_bytes: int


SEVERITY_LABELS = ("ok", "info", "warn", "high", "error", "crit")


class DashboardStats:
    def __init__(
        self,
        session: AsyncSession,
        *,
        issue_min_severity_rank: int | None = None,
    ) -> None:
        """Build a stats service.

        Stage 4 (audit follow-up): ``issue_min_severity_rank`` is the
        threshold used by ``overview()`` and ``sidebar_badges()`` to
        compute the ``issues_open`` count. If omitted, the legacy
        behaviour kicks in — anything past ``ok`` (rank > 10) counts,
        which matches the pre-Stage-4 wire-format. The API layer
        resolves the value from the configured runtime setting and
        passes it in.
        """
        self._session = session
        self._issue_min_rank = issue_min_severity_rank

    # ── Overview ────────────────────────────────────────────────
    async def overview(self) -> DashboardOverview:
        out = DashboardOverview()

        out.file_count = await self._scalar_count(MediaFile)
        out.library_count = await self._scalar_count(Library)
        out.integration_count = await self._scalar_count(Integration)
        out.integration_ok_count = await self._scalar_count(
            Integration, Integration.health_status == "ok"
        )
        out.rule_count = await self._scalar_count(Rule)
        out.rule_enabled_count = await self._scalar_count(
            Rule, Rule.enabled.is_(True)
        )

        out.severity_counts = await self._severity_counts(library_id=None)
        # Stage 4 (audit follow-up): ``issues_open`` is now computed
        # from a configurable threshold (default ``warn`` ⇒ rank 40)
        # rather than the legacy ``total - ok`` (which included ``info``).
        # The threshold is whitelisted by ``dashboard_issue_min_severity``
        # in the runtime settings schema; the API layer resolves it
        # and passes the rank into the constructor.
        if self._issue_min_rank is None:
            # Backwards-compatible default for callers that don't
            # opt in: anything past 'ok' (rank > 10) counts. Matches
            # the pre-Stage-4 wire-format exactly.
            out.issues_open = (
                out.severity_counts.total - out.severity_counts.ok
            )
        else:
            out.issues_open = await self._scalar_count(
                MediaFile, MediaFile.severity_rank >= self._issue_min_rank
            )

        out.optimization_counts = await self._optimization_counts()

        last_scan = await self._session.execute(
            select(func.max(ScanRun.finished_at)).where(
                ScanRun.status == "completed"
            )
        )
        out.last_scan_at = last_scan.scalar()

        # Stage 14.1: total bytes across all media files.
        size_row = await self._session.execute(
            select(func.coalesce(func.sum(MediaFile.size_bytes), 0))
        )
        out.total_size_bytes = int(size_row.scalar() or 0)

        return out

    # ── Per-library severity ────────────────────────────────────
    async def library_severity(self) -> list[LibrarySeverity]:
        # Per-(library, severity) counts, joined to library names. We use a
        # single grouped query rather than N queries per library.
        rows = await self._session.execute(
            select(
                Library.id,
                Library.name,
                MediaFile.severity,
                func.count(MediaFile.id),
            )
            .join(MediaFile, MediaFile.library_id == Library.id, isouter=True)
            .group_by(Library.id, Library.name, MediaFile.severity)
            .order_by(Library.name)
        )
        agg: dict[str, LibrarySeverity] = {}
        for library_id, name, severity, count in rows.all():
            entry = agg.get(library_id)
            if entry is None:
                entry = LibrarySeverity(
                    library_id=library_id,
                    library_name=name,
                    file_count=0,
                    severity=SeverityCounts(),
                )
                agg[library_id] = entry
            if severity is None:
                # Library with no files yet — Library row stays at zero.
                continue
            count = int(count)
            entry.file_count += count
            if severity in SEVERITY_LABELS:
                setattr(
                    entry.severity, severity, getattr(entry.severity, severity) + count
                )
                entry.severity.total += count
            else:
                # Future-proofing: unknown severity labels still count toward total.
                entry.severity.total += count
        return list(agg.values())

    # ── Integrations ────────────────────────────────────────────
    async def integration_health(self) -> list[IntegrationHealth]:
        rows = (
            await self._session.execute(
                select(Integration).order_by(Integration.name)
            )
        ).scalars().all()
        return [
            IntegrationHealth(
                integration_id=row.id,
                name=row.name,
                kind=row.kind,
                enabled=row.enabled,
                health_status=row.health_status,
                health_detail=row.health_detail,
                health_checked_at=row.health_checked_at,
            )
            for row in rows
        ]

    # ── Top rules ───────────────────────────────────────────────
    async def top_rules(self, *, limit: int = 5) -> list[TopRule]:
        """Rules ordered by how many files currently match them."""
        # COUNT over rule_evaluations grouped by rule_id, joined to rules.
        rows = await self._session.execute(
            select(
                Rule.id,
                Rule.name,
                Rule.enabled,
                func.count(RuleEvaluation.id).label("match_count"),
            )
            .join(
                RuleEvaluation,
                RuleEvaluation.rule_id == Rule.id,
                isouter=True,
            )
            .group_by(Rule.id, Rule.name, Rule.enabled)
            .order_by(func.count(RuleEvaluation.id).desc(), Rule.name)
            .limit(limit)
        )
        return [
            TopRule(
                rule_id=row[0],
                name=row[1],
                enabled=bool(row[2]),
                match_count=int(row[3]),
            )
            for row in rows.all()
        ]

    # ── Recent activity ─────────────────────────────────────────
    async def recent_scans(self, *, limit: int = 10) -> list[RecentScan]:
        rows = await self._session.execute(
            select(ScanRun, Library.name)
            .join(Library, Library.id == ScanRun.library_id, isouter=True)
            .order_by(ScanRun.created_at.desc())
            .limit(limit)
        )
        out: list[RecentScan] = []
        for scan, library_name in rows.all():
            out.append(
                RecentScan(
                    id=scan.id,
                    library_id=scan.library_id,
                    library_name=library_name or "(deleted)",
                    mode=scan.mode,
                    status=scan.status,
                    files_seen=scan.files_seen,
                    started_at=scan.started_at,
                    finished_at=scan.finished_at,
                )
            )
        return out

    async def recent_job_runs(self, *, limit: int = 10) -> list[RecentJobRun]:
        rows = (
            await self._session.execute(
                select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
            )
        ).scalars().all()
        return [
            RecentJobRun(
                id=row.id,
                job_kind=row.job_kind,
                status=row.status,
                trigger=row.trigger,
                started_at=row.started_at,
                duration_ms=row.duration_ms,
                error=row.error,
            )
            for row in rows
        ]

    # ── Sidebar badges ──────────────────────────────────────────
    async def sidebar_badges(self) -> dict[str, int]:
        """Numbers for the sidebar badge counters."""
        # Stage 4 (audit follow-up): use the configured threshold
        # when available; fall back to the legacy "anything past
        # ok" rule when the caller hasn't passed one. The fallback
        # exists so any internal callers that build a stats service
        # without an HTTP request handy still produce the historic
        # number.
        threshold_rank = (
            self._issue_min_rank if self._issue_min_rank is not None else 11
        )
        issues_open = await self._scalar_count(
            MediaFile, MediaFile.severity_rank >= threshold_rank
        )
        rules_enabled = await self._scalar_count(Rule, Rule.enabled.is_(True))
        active_optimizations = await self._scalar_count(
            OptimizationItem,
            OptimizationItem.status.in_(["queued", "running"]),
        )
        return {
            "issuesOpen": issues_open,
            "rulesEnabled": rules_enabled,
            "activeOptimizations": active_optimizations,
        }

    # ── Categories (Stage 26) ───────────────────────────────────
    async def categories(self, *, limit: int = 12) -> list["CategoryBreakdown"]:
        """Group media files by codec / container with size totals.

        Stage 26: surfaces the probed-metadata composition of the
        library — what codecs are in use, which containers dominate.
        Real data: ``video_codec`` and ``container`` come from
        ffprobe and have been on the ``media_files`` table since
        Stage 2.

        Returns up to ``limit`` rows per group, ordered by total
        size (the operational question is "what's eating my disk?",
        not "what's the longest tail of formats?"). NULL codecs /
        containers are collapsed into a single ``unknown`` bucket
        per group so they don't clutter the table — though when the
        ``unknown`` count is non-trivial it's a useful signal that
        the scanner couldn't probe a chunk of the library.

        The "group" field is the discriminator the UI uses to
        section the rows. We deliberately ship both video_codec and
        container groupings in one response — they're cheap
        aggregations and the dashboard renders them together.
        """
        out: list[CategoryBreakdown] = []
        for column, group in (
            (MediaFile.video_codec, "video_codec"),
            (MediaFile.container, "container"),
        ):
            rows = await self._session.execute(
                select(
                    column,
                    func.count(MediaFile.id),
                    func.coalesce(func.sum(MediaFile.size_bytes), 0),
                )
                .group_by(column)
                .order_by(func.coalesce(func.sum(MediaFile.size_bytes), 0).desc())
                .limit(limit)
            )
            for value, count, total_bytes in rows.all():
                key = value if value else "unknown"
                out.append(
                    CategoryBreakdown(
                        key=str(key),
                        label=str(key),
                        group=group,
                        file_count=int(count),
                        total_size_bytes=int(total_bytes or 0),
                    )
                )
        return out

    # ── Internals ───────────────────────────────────────────────
    async def _severity_counts(
        self, *, library_id: str | None
    ) -> SeverityCounts:
        stmt = select(MediaFile.severity, func.count(MediaFile.id)).group_by(
            MediaFile.severity
        )
        if library_id is not None:
            stmt = stmt.where(MediaFile.library_id == library_id)
        rows = await self._session.execute(stmt)
        counts = SeverityCounts()
        for severity, count in rows.all():
            count = int(count)
            counts.total += count
            if severity in SEVERITY_LABELS:
                setattr(counts, severity, getattr(counts, severity) + count)
        return counts

    async def _optimization_counts(self) -> OptimizationCounts:
        rows = await self._session.execute(
            select(OptimizationItem.status, func.count(OptimizationItem.id))
            .group_by(OptimizationItem.status)
        )
        out = OptimizationCounts()
        for status, count in rows.all():
            count = int(count)
            if status == "queued":
                out.queued = count
            elif status == "running":
                out.running = count
            elif status == "completed":
                out.completed = count
            elif status == "failed":
                out.failed = count
        return out

    async def _scalar_count(self, model, *where) -> int:
        stmt = select(func.count()).select_from(model)
        for w in where:
            stmt = stmt.where(w)
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    # ── Stage 14.1: 30-day series for sparklines ──────────────
    async def series(self, days: int = 30) -> "DashboardSeries":
        """Daily rollups of scan/job activity for the last ``days`` days.

        For metrics we genuinely have history on (scans, completed
        runs), we group by day-bucket. For metrics with no daily
        snapshot store (severity / integrity), we emit a flat array
        of the current value — honest placeholder until a snapshot
        table lands.
        """
        days = max(1, min(days, 90))
        now = utcnow()
        # Day buckets [today-days+1 .. today], oldest first.
        buckets = [
            (now - _dt.timedelta(days=days - 1 - i)).date()
            for i in range(days)
        ]

        # Scans-per-day → files_seen.
        scan_rows = await self._session.execute(
            select(ScanRun.finished_at, ScanRun.files_seen).where(
                ScanRun.finished_at.is_not(None),
                ScanRun.finished_at >= now - _dt.timedelta(days=days),
            )
        )
        files_seen_by_day: dict[_dt.date, int] = {b: 0 for b in buckets}
        for finished_at, files_seen in scan_rows.all():
            if finished_at is None:
                continue
            d = finished_at.date()
            if d in files_seen_by_day:
                files_seen_by_day[d] += int(files_seen or 0)

        # Job-runs-per-day. ``rule_evaluation_tick`` runs serve as a
        # proxy for "issues processed" if rules are enabled.
        run_rows = await self._session.execute(
            select(JobRun.started_at, JobRun.status, JobRun.job_kind).where(
                JobRun.started_at >= now - _dt.timedelta(days=days),
            )
        )
        opened_by_day: dict[_dt.date, int] = {b: 0 for b in buckets}
        resolved_by_day: dict[_dt.date, int] = {b: 0 for b in buckets}
        for started_at, status, job_kind in run_rows.all():
            d = started_at.date()
            if d not in opened_by_day:
                continue
            if status == "completed":
                resolved_by_day[d] += 1
            elif status in ("failed", "error"):
                opened_by_day[d] += 1
            # Note: in a real system we'd index a daily-snapshot table.
            # For now this is "activity happened" not "issues changed".
            if job_kind and "rule" in job_kind.lower():
                opened_by_day[d] += 1

        # Integrity score: flat array at the current value. We can't
        # back-compute historical severities without a snapshot store.
        current = await self._severity_counts(library_id=None)
        integrity_now = (
            (current.ok / current.total) * 100.0 if current.total else 100.0
        )

        return DashboardSeries(
            days=days,
            issues_opened=[opened_by_day[b] for b in buckets],
            issues_resolved=[resolved_by_day[b] for b in buckets],
            integrity_score=[round(integrity_now, 2)] * days,
            files_seen=[files_seen_by_day[b] for b in buckets],
        )


# ── Stage 14.1: dashboard time-series ────────────────────────
@dataclass(slots=True)
class DashboardSeries:
    """30-day-ish daily rollup for sparkline charts on the dashboard.

    Every list has the same length (``days``). Indexes go oldest→newest
    so the frontend can plot straight from index 0.

    For metrics where we have no per-day historical store (we don't yet
    snapshot severity counts daily), we return a flat array of the
    current value rather than fabricate trends. The frontend treats
    flat arrays the same as empty and just doesn't draw a curve.
    """

    days: int
    issues_opened: list[int] = field(default_factory=list)
    issues_resolved: list[int] = field(default_factory=list)
    integrity_score: list[float] = field(default_factory=list)
    files_seen: list[int] = field(default_factory=list)


# Re-export the model class as module name for backward compat with callers
# expecting ``dashboard.DashboardStats``.
__all__ = [
    "CategoryBreakdown",
    "DashboardOverview",
    "DashboardSeries",
    "DashboardStats",
    "IntegrationHealth",
    "LibrarySeverity",
    "OptimizationCounts",
    "RecentJobRun",
    "RecentScan",
    "SeverityCounts",
    "TopRule",
]


# Silence linters about utcnow being unused — kept for follow-up "freshness"
# columns we'll add when dashboards grow time-windowed widgets.
_ = utcnow
