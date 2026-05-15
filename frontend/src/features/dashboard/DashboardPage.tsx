/**
 * Dashboard page.
 *
 * Stage 26 modernization keeps the operational architecture intact
 * — same hooks, same backend endpoints, same data shapes — and
 * makes the surface denser and more drill-down-friendly:
 *
 *   - Range toggle (7d / 30d / 90d) lives in the page header,
 *     pulling the existing ``/dashboard/series?days=N`` endpoint
 *     for the chosen window. Delta math scales with the window.
 *   - Library composition card (Stage 26 backend endpoint):
 *     real codec / container breakdowns sourced from probed
 *     metadata. See ``CategoriesCard.tsx``.
 *   - Library rows drill into Files filtered by that library
 *     (Stage 26 also extends Files to honor ``?library_id``).
 *   - Recent scans and recent automation runs use the Stage 23
 *     ``.files-table`` vocab — denser, sortable-looking, matching
 *     the rest of the operational pages.
 *
 * What's deliberately NOT shipped from the prototype: top
 * transcoded files and the codec × device matrix. Auditarr has no
 * play-tracking subsystem, and faking those panels would violate
 * the project's "no invented data" discipline.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import { ScanProgressBar } from "@/components/ui/ScanProgressBar";
import { SeverityHeatmap } from "@/components/ui/SeverityHeatmap";
import { Sparkline } from "@/components/ui/Sparkline";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  useDashboardIntegrations,
  useDashboardLibraries,
  useDashboardOverview,
  useDashboardRecentJobRuns,
  useDashboardRecentScans,
  useDashboardSeries,
  useDashboardTopRules,
  type LibrarySeverity,
  type RecentJobRun,
  type RecentScan,
  type SeverityCounts,
} from "@/hooks/useDashboard";
import { useHelpKey } from "@/hooks/useHelpKey";
import { useLibraries, useTriggerScan, useTriggerScanAll } from "@/hooks/useMedia";
import { useScanProgress } from "@/hooks/useScanProgress";
import { type RuleSuggestion } from "@/hooks/useRules";
import { cn } from "@/lib/cn";
import { fmtBytes, fmtNum } from "@/lib/format";
import { toast } from "@/lib/toast";
import { useAuthStore } from "@/stores/authStore";
import { useUiStore } from "@/stores/uiStore";

import { PlaybackStatsCard } from "@/features/playback/PlaybackStatsCard";

import { CategoriesCard } from "./CategoriesCard";
import { RangeToggle, type RangeDays } from "./RangeToggle";
import { SuggestionReviewModal } from "./SuggestionReviewModal";
import { SuggestionsCard } from "./SuggestionsCard";

const SEVERITY_KEYS: (keyof SeverityCounts)[] = [
  "ok",
  "info",
  "warn",
  "high",
  "error",
  "crit",
];
const SEVERITY_LABELS: Record<keyof SeverityCounts, string> = {
  ok: "OK",
  info: "Info",
  warn: "Warning",
  high: "High",
  error: "Error",
  crit: "Critical",
  total: "Total",
};

export function DashboardPage() {
  useHelpKey("dashboard.overview");

  const [range, setRange] = useState<RangeDays>(30);

  const overview = useDashboardOverview();
  const series = useDashboardSeries(range);
  const libraries = useDashboardLibraries();
  const integrations = useDashboardIntegrations();
  const topRules = useDashboardTopRules(5);
  const recentScans = useDashboardRecentScans(8);
  const recentJobs = useDashboardRecentJobRuns(8);
  const navigate = useNavigate();

  // Stage 16: which suggestion is being reviewed (if any).
  const [reviewing, setReviewing] = useState<RuleSuggestion | null>(null);

  // Stage 7 audit fix (Issue 7): scan controls live on the
  // Dashboard too, not just the Files page. Three pieces:
  //   - ``useLibraries`` — to populate the picker
  //   - ``useTriggerScan`` — mutation that POSTs the scan
  //   - ``useScanProgress`` — WS-backed status pill
  // Identical contract to FilesPage; no new hooks created.
  const allLibraries = useLibraries();
  const triggerScan = useTriggerScan();
  // Stage 17 (audit follow-up): admin-gated "Scan all libraries"
  // button. Reuses the existing useTriggerScanAll mutation that the
  // Files page already calls — this just surfaces the same affordance
  // on the dashboard so operators don't have to leave home to kick
  // off a global scan.
  const triggerScanAll = useTriggerScanAll();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const scanProgress = useScanProgress();
  const [scanLibraryId, setScanLibraryId] = useState<string>("");

  // Auto-select the first enabled library once the list loads
  // so the Run-scan button is immediately usable in the common
  // single-library case. We don't overwrite a manual selection.
  useEffect(() => {
    if (scanLibraryId) return;
    const list = allLibraries.data;
    if (!list || list.length === 0) return;
    const firstEnabled = list.find((l) => l.enabled) ?? list[0];
    if (firstEnabled) setScanLibraryId(firstEnabled.id);
  }, [allLibraries.data, scanLibraryId]);

  // Stage 11 audit fix (Issue 16): persisted collapse state per
  // dashboard section. The page only wires the chevron into
  // sections whose CardHead is rendered inline here (i.e. owned
  // by DashboardPage). Sub-component cards (Categories,
  // Suggestions, Recent scans/jobs) render their own CardHead
  // inside their own files and aren't part of this stage's scope.
  const dashboardHidden = useUiStore((s) => s.dashboardHidden);
  const toggleSection = useUiStore((s) => s.toggleDashboardSection);
  const resetLayout = useUiStore((s) => s.resetDashboardLayout);
  const isHidden = (key: string) => dashboardHidden.includes(key);

  // Stage 14.1: derive integrity score client-side from severity counts.
  const integrityScore = overview.data
    ? overview.data.severity_counts.total > 0
      ? (overview.data.severity_counts.ok /
          overview.data.severity_counts.total) *
        100
      : 100
    : null;

  // Stage 26: delta vs prior comparable window — scales with range.
  // For a 7d window, comparing day N vs the prior 2 days' average is
  // too jittery; we fall back to the static "of N files" tile in
  // that case. For 30d / 90d the prior 7d / 14d averages are stable.
  const issuesOpenedDelta = (() => {
    if (!series.data?.issues_opened?.length) return null;
    if (range === 7) return null;
    const arr = series.data.issues_opened;
    const recent = arr.slice(-1)[0] ?? 0;
    const priorWindow = range === 90 ? 14 : 7;
    const prior = arr.slice(-(priorWindow + 1), -1);
    if (prior.length === 0) return null;
    const avg = prior.reduce((a, b) => a + b, 0) / prior.length;
    return recent - avg;
  })();

  const severityData = overview.data
    ? SEVERITY_KEYS.map((key) => ({
        key,
        label: SEVERITY_LABELS[key],
        count: overview.data!.severity_counts[key],
        color: `sev-${key}`,
      }))
    : [];

  return (
    <>
      <PageHeader
        title="Dashboard"
        sub="Library health at a glance"
        helpKey="dashboard.overview"
        actions={
          // Stage 7: scan controls live alongside the existing
          // range toggle. Layout is a single flex row with small
          // gaps so it matches the FilesPage header rhythm. The
          // picker is hidden when there are zero libraries — in
          // that case the operator's next step is in Settings, not
          // here, and a disabled "Run scan" button alone communicates
          // that more clearly than a button + an empty <select>.
          <div className="flex items-center gap-2 flex-wrap">
            {dashboardHidden.length > 0 ? (
              // Stage 11 audit fix (Issue 16): reset link only
              // appears when at least one section is collapsed.
              // Keeps the header uncluttered in the default state.
              <Button
                size="sm"
                variant="ghost"
                onClick={resetLayout}
                title="Restore all dashboard sections"
              >
                <Icon name="refresh" size={12} />
                <span className="ml-1">Reset layout</span>
              </Button>
            ) : null}
            {/* Stage 8 (audit follow-up): the two inline Pills were
                yes/no spinners; ScanProgressBar shows actual
                files_seen / files_total_estimate + percent driven by
                ``scan.progress`` WS events. */}
            <ScanProgressBar />
            {allLibraries.data && allLibraries.data.length > 0 ? (
              <select
                className="settings-input"
                value={scanLibraryId}
                onChange={(e) => setScanLibraryId(e.target.value)}
                aria-label="Library to scan"
                disabled={triggerScan.isPending}
              >
                {allLibraries.data.map((lib) => (
                  <option key={lib.id} value={lib.id} disabled={!lib.enabled}>
                    {lib.name}
                    {!lib.enabled ? " (disabled)" : ""}
                  </option>
                ))}
              </select>
            ) : null}
            <Button
              size="sm"
              variant="primary"
              disabled={
                !scanLibraryId ||
                triggerScan.isPending ||
                !!scanProgress.runId
              }
              onClick={() =>
                scanLibraryId && triggerScan.mutate({ libraryId: scanLibraryId })
              }
              title={
                scanLibraryId
                  ? "Scan the selected library"
                  : "Add a library in Settings first"
              }
            >
              <Icon name="play" size={12} />
              <span className="ml-1">
                {triggerScan.isPending ? "Scanning…" : "Run scan"}
              </span>
            </Button>
            {/* Stage 17 (audit follow-up): admin-only "Scan all
                libraries" surfaced alongside Run scan. Same mutation
                the Files page button uses. Disabled while a scan is
                in progress so operators don't double-queue. */}
            {isAdmin ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={
                  triggerScanAll.isPending || !!scanProgress.runId
                }
                onClick={() =>
                  triggerScanAll.mutate(
                    {},
                    {
                      onSuccess: (runs) => {
                        toast(
                          `Queued scan for ${runs.length} ${runs.length === 1 ? "library" : "libraries"}`,
                          "ok",
                        );
                      },
                      onError: (err) => {
                        toast(
                          `Scan-all failed: ${(err as Error).message}`,
                          "error",
                        );
                      },
                    },
                  )
                }
                title="Queue a scan for every enabled library"
                aria-label="Scan all libraries"
              >
                <Icon name="refresh" size={12} />
                <span className="ml-1">
                  {triggerScanAll.isPending ? "Queueing…" : "Scan all"}
                </span>
              </Button>
            ) : null}
            <RangeToggle value={range} onChange={setRange} />
          </div>
        }
      />
      <div className="p-6 flex flex-col gap-6">
        {/* ── Overview metrics ── */}
        {overview.isLoading ? (
          <LoadingState label="Loading dashboard…" />
        ) : overview.isError ? (
          <ErrorState
            title="Failed to load dashboard"
            description={(overview.error as Error)?.message}
          />
        ) : overview.data ? (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Metric
                label="Files audited"
                value={fmtNum(overview.data.file_count)}
                icon="files"
                detail={`across ${fmtNum(overview.data.library_count)} ${
                  overview.data.library_count === 1 ? "library" : "libraries"
                }`}
                sparkSeries={series.data?.files_seen}
                to="/files"
              />
              <Metric
                label="Library size"
                value={fmtBytes(overview.data.total_size_bytes)}
                icon="folder"
                detail={`${fmtNum(overview.data.severity_counts.total)} indexed files`}
                to="/files"
              />
              <Metric
                label="Integrity score"
                value={
                  integrityScore != null
                    ? `${integrityScore.toFixed(1)}%`
                    : "—"
                }
                icon="check"
                detail={
                  overview.data.severity_counts.total > 0
                    ? `${fmtNum(overview.data.severity_counts.ok)} files passing`
                    : "no files scanned yet"
                }
                sparkSeries={series.data?.integrity_score}
                sparkAccent
                tone={
                  integrityScore != null && integrityScore >= 95
                    ? "ok"
                    : undefined
                }
              />
              <Metric
                label="Open issues"
                value={fmtNum(overview.data.issues_open)}
                icon="alert"
                detail={
                  issuesOpenedDelta != null
                    ? issuesOpenedDelta > 0
                      ? `↑ ${issuesOpenedDelta.toFixed(1)} vs prior avg`
                      : issuesOpenedDelta < 0
                        ? `↓ ${Math.abs(issuesOpenedDelta).toFixed(1)} vs prior avg`
                        : `steady vs prior avg`
                    : `of ${fmtNum(overview.data.severity_counts.total)} files`
                }
                sparkSeries={series.data?.issues_opened}
                tone={overview.data.issues_open > 0 ? "warn" : "ok"}
                to="/files"
              />
            </div>

            {/* Stage 7 audit fix (Issue 7): "Last scanned" line lives
                right under the tile grid so an operator opening the
                dashboard sees, at a glance: how big the library is,
                how healthy it is, and how fresh the data is. The
                value comes from the existing /dashboard/overview
                shape — no new endpoint. */}
            <div className="text-[11.5px] text-muted-2 -mt-2">
              {overview.data.last_scan_at
                ? `Last scanned ${formatTimeAgo(overview.data.last_scan_at)}`
                : "No scans yet — run one above to populate."}
            </div>

            {/* ── Severity heatmap ── */}
            <Card>
              <CardHead
                title="Severity distribution"
                subtitle={`${fmtNum(overview.data.severity_counts.total)} files across all libraries`}
                actions={
                  <CollapseChevron
                    hidden={isHidden("severity")}
                    onClick={() => toggleSection("severity")}
                    label="Severity distribution"
                  />
                }
              />
              {!isHidden("severity") ? (
                <CardBody className="py-3">
                  {overview.data.severity_counts.total > 0 ? (
                    <SeverityHeatmap
                      data={severityData}
                      onPick={(s) => navigate(`/files?severity=${s.key}`)}
                    />
                  ) : (
                    <div className="text-[12.5px] text-muted-2">
                      No files yet — add a library and run a scan.
                    </div>
                  )}
                </CardBody>
              ) : null}
            </Card>
          </>
        ) : null}

        {/* ── Stage 26: library composition ── */}
        <CategoriesCard />

        {/* ── Stage 12 (audit follow-up): playback insights ── */}
        <PlaybackStatsCard />

        {/* ── Stage 16: rule suggestions ── */}
        <SuggestionsCard onReview={(s) => setReviewing(s)} />

        {/* ── Two-column row: libraries + integrations ──
            Stage 6 (audit follow-up): when EXACTLY ONE of the paired
            cards is collapsed, expand the survivor to full row width.
            Pre-Stage-6, ``xl:grid-cols-2`` left a phantom empty
            column where the collapsed card used to sit. When BOTH
            are collapsed, the row is just headers and 2-col looks
            fine (each header is a one-line strip). */}
        <div
          className={cn(
            "grid grid-cols-1 gap-4",
            isHidden("libraries") !== isHidden("integrations")
              ? "xl:grid-cols-1"
              : "xl:grid-cols-2",
          )}
        >
          <Card>
            <CardHead
              title="Libraries"
              subtitle={
                libraries.data ? `${libraries.data.length} configured` : undefined
              }
              actions={
                <CollapseChevron
                  hidden={isHidden("libraries")}
                  onClick={() => toggleSection("libraries")}
                  label="Libraries"
                />
              }
            />
            {!isHidden("libraries") ? (
              <CardBodyFlush>
                {libraries.isLoading ? (
                  <div className="px-4 py-6">
                    <LoadingState label="Loading…" />
                  </div>
                ) : !libraries.data || libraries.data.length === 0 ? (
                  <div className="px-4 py-6">
                    <EmptyState
                      icon="files"
                      title="No libraries yet"
                      description="Add a library in Settings to start scanning."
                    />
                  </div>
                ) : (
                  libraries.data.map((row) => (
                    <LibraryRow key={row.library_id} library={row} />
                  ))
                )}
              </CardBodyFlush>
            ) : null}
          </Card>

          <Card>
            <CardHead
              title="Integrations"
              subtitle={
                integrations.data
                  ? `${integrations.data.length} configured`
                  : undefined
              }
              actions={
                <CollapseChevron
                  hidden={isHidden("integrations")}
                  onClick={() => toggleSection("integrations")}
                  label="Integrations"
                />
              }
            />
            {!isHidden("integrations") ? (
              <CardBodyFlush>
                {integrations.isLoading ? (
                  <div className="px-4 py-6">
                    <LoadingState label="Loading…" />
                  </div>
                ) : !integrations.data || integrations.data.length === 0 ? (
                  <div className="px-4 py-6">
                    <EmptyState
                      icon="integrations"
                      title="No integrations connected"
                      description="Connect Plex, Sonarr, or Bazarr in Integrations."
                    />
                  </div>
                ) : (
                  integrations.data.map((row) => (
                    <div
                      key={row.integration_id}
                      className="px-4 py-2.5 border-b border-border last:border-b-0 flex items-center gap-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] font-medium truncate">
                            {row.name}
                          </span>
                          <Tag>{row.kind}</Tag>
                          {!row.enabled ? <Pill>disabled</Pill> : null}
                        </div>
                        {row.health_detail ? (
                          <div className="text-[11px] text-muted-2 mt-0.5 truncate">
                            {row.health_detail}
                          </div>
                        ) : null}
                      </div>
                      <Pill className={healthClass(row.health_status)}>
                        {row.health_status}
                      </Pill>
                    </div>
                  ))
                )}
              </CardBodyFlush>
            ) : null}
          </Card>
        </div>

        {/* ── Top rules ── */}
        <Card>
          <CardHead
            title="Top rules by match count"
            actions={
              <CollapseChevron
                hidden={isHidden("top-rules")}
                onClick={() => toggleSection("top-rules")}
                label="Top rules"
              />
            }
          />
          {!isHidden("top-rules") ? (
            <CardBodyFlush>
              {topRules.isLoading ? (
                <div className="px-4 py-6">
                  <LoadingState label="Loading…" />
                </div>
              ) : !topRules.data || topRules.data.length === 0 ? (
                <div className="px-4 py-6">
                  <EmptyState
                    icon="rules"
                    title="No rules to rank yet"
                    description="Create a rule to see how many files it touches."
                  />
                </div>
              ) : (
                topRules.data.map((rule) => (
                  <div
                    key={rule.rule_id}
                    className="px-4 py-2.5 border-b border-border last:border-b-0 flex items-center gap-3"
                  >
                    <Icon name="rules" size={14} className="text-muted-2" />
                    <Link
                      to="/rules"
                      className="text-[13px] font-medium truncate flex-1 hover:underline"
                    >
                      {rule.name}
                    </Link>
                    {!rule.enabled ? <Pill>disabled</Pill> : null}
                    <Tag>{fmtNum(rule.match_count)} matches</Tag>
                  </div>
                ))
              )}
            </CardBodyFlush>
          ) : null}
        </Card>

        {/* ── Recent activity ──
            Stage 6 (audit follow-up): same dynamic-col logic as
            the libraries/integrations row above. */}
        <div
          className={cn(
            "grid grid-cols-1 gap-4",
            isHidden("recent-scans") !== isHidden("recent-jobs")
              ? "xl:grid-cols-1"
              : "xl:grid-cols-2",
          )}
        >
          <RecentScansCard
            data={recentScans.data}
            isLoading={recentScans.isLoading}
            hidden={isHidden("recent-scans")}
            onToggle={() => toggleSection("recent-scans")}
            onSelect={(id) => navigate(`/scans/${encodeURIComponent(id)}`)}
          />
          <RecentJobsCard
            data={recentJobs.data}
            isLoading={recentJobs.isLoading}
            hidden={isHidden("recent-jobs")}
            onToggle={() => toggleSection("recent-jobs")}
          />
        </div>
      </div>

      {reviewing ? (
        <SuggestionReviewModal
          suggestion={reviewing}
          onClose={() => setReviewing(null)}
        />
      ) : null}
    </>
  );
}

// ── Metric tile ─────────────────────────────────────────────
function Metric({
  label,
  value,
  icon,
  detail,
  tone,
  to,
  sparkSeries,
  sparkAccent,
}: {
  label: string;
  value: string;
  icon: IconName;
  detail?: string;
  tone?: "ok" | "warn";
  to?: string;
  sparkSeries?: number[];
  sparkAccent?: boolean;
}) {
  // Only render a sparkline if the series has variation — flat
  // arrays (which we get for metrics with no daily snapshot store)
  // would just draw a horizontal line, so we skip them.
  const showSpark =
    sparkSeries && sparkSeries.length > 1 && new Set(sparkSeries).size > 1;
  const inner = (
    <Card className="h-full">
      <CardBody className="py-3">
        <div className="flex items-center gap-2 text-muted-2">
          <Icon name={icon} size={13} />
          <span className="text-[11px] uppercase tracking-[0.06em] font-semibold">
            {label}
          </span>
        </div>
        <div
          className={cn(
            "mt-1 text-[22px] font-semibold tabular-nums",
            tone === "warn" && "text-sev-warn",
            tone === "ok" && "text-sev-ok",
          )}
        >
          {value}
        </div>
        {showSpark ? (
          <Sparkline values={sparkSeries!} accent={sparkAccent} height={28} />
        ) : null}
        {detail ? (
          <div className="text-[11.5px] text-muted-2 mt-0.5">{detail}</div>
        ) : null}
      </CardBody>
    </Card>
  );
  if (to) {
    return (
      <Link to={to} className="block hover:opacity-90 transition-opacity">
        {inner}
      </Link>
    );
  }
  return inner;
}

// ── Library row (severity bar inside Libraries card) ─────────
// Stage 26: rows are now links into Files filtered by that library
// — closing the dashboard → operations drill-down loop. Files page
// extension to honor ``?library_id`` is the other half of this.
function LibraryRow({ library }: { library: LibrarySeverity }) {
  return (
    <Link
      to={`/files?library_id=${encodeURIComponent(library.library_id)}`}
      className="block px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-[var(--hover)] transition-colors"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <Icon name="files" size={13} className="text-muted-2" />
        <span className="text-[13px] font-medium truncate">
          {library.library_name}
        </span>
        <Tag>{fmtNum(library.file_count)} files</Tag>
      </div>
      {library.file_count > 0 ? (
        <div className="flex h-1.5 w-full overflow-hidden rounded-md">
          {SEVERITY_KEYS.map((key) => {
            const count = library.severity[key];
            if (count === 0) return null;
            const pct = (count / library.severity.total) * 100;
            return (
              <div
                key={key}
                className={cn("h-full", severityFill(key))}
                style={{ width: `${pct}%` }}
                title={`${key}: ${count}`}
              />
            );
          })}
        </div>
      ) : (
        <div className="text-[11px] text-muted-2">No files yet</div>
      )}
    </Link>
  );
}

// ── Stage 26: Recent scans / Recent jobs — table-styled ──────
// Earlier shape was a vertical stack of card-rows; the modernized
// dashboard uses dense ``.files-table`` rows matching Stage 23.
// No new CSS — pure reuse.

function RecentScansCard({
  data,
  isLoading,
  hidden,
  onToggle,
  onSelect,
}: {
  data: RecentScan[] | undefined;
  isLoading: boolean;
  hidden: boolean;
  onToggle: () => void;
  /** Stage 14 (audit follow-up): row click opens
   *  ``/scans/:scanId``. The parent owns the navigate call so this
   *  card can stay presentational. */
  onSelect: (scanId: string) => void;
}) {
  return (
    <Card>
      <CardHead
        title="Recent scans"
        actions={
          <CollapseChevron
            hidden={hidden}
            onClick={onToggle}
            label="Recent scans"
          />
        }
      />
      {!hidden ? (
        isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : !data || data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="files"
              title="No scans yet"
              description="Run a scan from the Files page."
            />
          </div>
        ) : (
          <div className="files-table-wrap">
            <table className="files-table" role="grid">
              <thead>
                <tr>
                  <th>Library</th>
                  <th>Mode</th>
                  <th>Status</th>
                  <th className="num">Files</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.map((scan) => (
                  <tr
                    key={scan.id}
                    className="files-table-row cursor-pointer hover:bg-[var(--hover)]"
                    onClick={() => onSelect(scan.id)}
                    tabIndex={0}
                    role="button"
                    aria-label={`Open scan ${scan.library_name}`}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(scan.id);
                      }
                    }}
                  >
                    <td>
                      <div className="text-[12.5px] font-medium truncate">
                        {scan.library_name}
                      </div>
                    </td>
                    <td>
                      <Tag>{scan.mode}</Tag>
                    </td>
                    <td>
                      <Pill className={healthClass(scan.status)}>
                        {scan.status}
                      </Pill>
                    </td>
                    <td className="num font-mono">{fmtNum(scan.files_seen)}</td>
                    <td className="text-[11.5px] text-muted-2">
                      {formatWhen(scan.finished_at ?? scan.started_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </Card>
  );
}

function RecentJobsCard({
  data,
  isLoading,
  hidden,
  onToggle,
}: {
  data: RecentJobRun[] | undefined;
  isLoading: boolean;
  hidden: boolean;
  onToggle: () => void;
}) {
  return (
    <Card>
      <CardHead
        title="Recent automation runs"
        actions={
          <CollapseChevron
            hidden={hidden}
            onClick={onToggle}
            label="Recent automation runs"
          />
        }
      />
      {!hidden ? (
        isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : !data || data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="clock"
              title="No runs yet"
              description="Job runs appear here once schedules fire or you run them manually."
            />
          </div>
        ) : (
          <div className="files-table-wrap">
            <table className="files-table" role="grid">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Trigger</th>
                  <th>Status</th>
                  <th>Duration</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {data.map((run) => (
                  <tr key={run.id} className="files-table-row">
                    <td>
                      <div className="text-[12.5px] font-medium truncate">
                        {run.job_kind}
                      </div>
                      {run.error ? (
                        <div
                          className="text-[11px] text-sev-error truncate"
                          title={run.error}
                        >
                          {run.error}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <Tag>{run.trigger}</Tag>
                    </td>
                    <td>
                      <Pill className={healthClass(run.status)}>{run.status}</Pill>
                    </td>
                    <td className="font-mono text-[11.5px]">
                      {run.duration_ms !== null
                        ? formatDuration(run.duration_ms)
                        : "—"}
                    </td>
                    <td className="text-[11.5px] text-muted-2">
                      {formatWhen(run.started_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </Card>
  );
}

// ── helpers ─────────────────────────────────────────────────
function severityFill(key: keyof SeverityCounts): string {
  switch (key) {
    case "ok":
      return "bg-sev-ok";
    case "info":
      return "bg-sev-info";
    case "warn":
      return "bg-sev-warn";
    case "high":
      return "bg-sev-high";
    case "error":
      return "bg-sev-error";
    case "crit":
      return "bg-sev-crit";
    default:
      return "bg-border";
  }
}

function healthClass(status: string): string {
  switch (status) {
    case "ok":
    case "completed":
      return "text-sev-ok border-sev-ok/40 bg-sev-ok/10";
    case "degraded":
    case "warn":
    case "running":
    case "queued":
      return "text-sev-warn border-sev-warn/40 bg-sev-warn/10";
    case "error":
    case "failed":
    case "crit":
      return "text-sev-error border-sev-error/40 bg-sev-error/10";
    default:
      return "";
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

// Stage 11 audit fix (Issue 16): a small chevron button used in
// each collapsible CardHead's actions slot. ``hidden`` flips the
// chevron direction so the affordance reads correctly in both
// states (down = expanded / "I'll close this"; right = collapsed
// / "I'll open this"). Keeping it inline rather than promoting
// to a shared primitive — it's three lines of JSX and only one
// page uses it.
function CollapseChevron({
  hidden,
  onClick,
  label,
}: {
  hidden: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 text-muted-2 hover:text-text"
      aria-label={hidden ? `Expand ${label}` : `Collapse ${label}`}
      aria-expanded={!hidden}
      title={hidden ? "Expand" : "Collapse"}
    >
      <Icon name={hidden ? "chev_right" : "chev_down"} size={14} />
    </button>
  );
}

// Stage 7 audit fix (Issue 7): relative formatter for the
// "Last scanned" hint. We resolve the standard rough buckets
// (seconds → minutes → hours → days) so the operator gets an
// "X ago" reading without a date library. Anything older than
// 30 days falls back to the absolute date — the goal at that
// horizon is "see the date" not "see the relative gap".
function formatTimeAgo(value: string): string {
  const t = new Date(value).getTime();
  if (Number.isNaN(t)) return value;
  const diffSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? "" : "s"} ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`;
  return `on ${new Date(t).toLocaleDateString()}`;
}
