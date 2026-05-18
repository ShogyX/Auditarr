import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface SeverityCounts {
  ok: number;
  info: number;
  warn: number;
  high: number;
  error: number;
  crit: number;
  total: number;
}

export interface OptimizationCounts {
  queued: number;
  running: number;
  completed: number;
  failed: number;
}

export interface DashboardOverview {
  file_count: number;
  library_count: number;
  integration_count: number;
  integration_ok_count: number;
  rule_count: number;
  rule_enabled_count: number;
  severity_counts: SeverityCounts;
  issues_open: number;
  optimization_counts: OptimizationCounts;
  last_scan_at: string | null;
  total_size_bytes: number;
}

export interface DashboardSeries {
  days: number;
  issues_opened: number[];
  issues_resolved: number[];
  integrity_score: number[];
  files_seen: number[];
}

export interface LibrarySeverity {
  library_id: string;
  library_name: string;
  file_count: number;
  severity: SeverityCounts;
}

export interface IntegrationHealth {
  integration_id: string;
  name: string;
  kind: string;
  enabled: boolean;
  health_status: string;
  health_detail: string | null;
  health_checked_at: string | null;
}

export interface TopRule {
  rule_id: string;
  name: string;
  enabled: boolean;
  match_count: number;
}

export interface RecentScan {
  id: string;
  library_id: string;
  library_name: string;
  mode: string;
  status: string;
  files_seen: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface RecentJobRun {
  id: string;
  job_kind: string;
  status: string;
  trigger: string;
  started_at: string;
  duration_ms: number | null;
  error: string | null;
}

export interface SidebarBadges {
  issuesOpen: number;
  rulesEnabled: number;
  activeOptimizations: number;
}

// Stage 26: library composition by codec / container.
export type CategoryGroup = "video_codec" | "container";

export interface CategoryBreakdown {
  key: string;
  label: string;
  group: CategoryGroup;
  file_count: number;
  total_size_bytes: number;
}

// ── Hooks ─────────────────────────────────────────────────────
export function useDashboardOverview() {
  return useQuery({
    queryKey: ["dashboard", "overview"],
    queryFn: () => apiClient.get<DashboardOverview>("/dashboard/overview"),
    staleTime: 15_000,
  });
}

export function useDashboardSeries(days = 30) {
  return useQuery({
    queryKey: ["dashboard", "series", days],
    queryFn: () => apiClient.get<DashboardSeries>(`/dashboard/series?days=${days}`),
    staleTime: 60_000,
  });
}

export function useDashboardLibraries() {
  return useQuery({
    queryKey: ["dashboard", "libraries"],
    queryFn: () => apiClient.get<LibrarySeverity[]>("/dashboard/libraries"),
    staleTime: 30_000,
  });
}

export function useDashboardIntegrations() {
  return useQuery({
    queryKey: ["dashboard", "integrations"],
    queryFn: () => apiClient.get<IntegrationHealth[]>("/dashboard/integrations"),
    staleTime: 30_000,
  });
}

export function useDashboardTopRules(limit = 5) {
  return useQuery({
    queryKey: ["dashboard", "top-rules", limit],
    queryFn: () => apiClient.get<TopRule[]>(`/dashboard/top-rules?limit=${limit}`),
    staleTime: 30_000,
  });
}

export function useDashboardRecentScans(limit = 5) {
  return useQuery({
    queryKey: ["dashboard", "recent-scans", limit],
    queryFn: () => apiClient.get<RecentScan[]>(`/dashboard/recent-scans?limit=${limit}`),
    staleTime: 15_000,
  });
}

export function useDashboardRecentJobRuns(limit = 5) {
  return useQuery({
    queryKey: ["dashboard", "recent-job-runs", limit],
    queryFn: () => apiClient.get<RecentJobRun[]>(`/dashboard/recent-job-runs?limit=${limit}`),
    staleTime: 15_000,
  });
}

export function useSidebarBadges() {
  return useQuery({
    queryKey: ["dashboard", "sidebar-badges"],
    queryFn: () => apiClient.get<SidebarBadges>("/dashboard/sidebar-badges"),
    staleTime: 30_000,
    // Slow refetch so the sidebar doesn't flicker; user actions invalidate
    // explicitly via mutation onSuccess.
    refetchInterval: 60_000,
  });
}

// Stage 26: library composition.
export function useDashboardCategories(limit = 12) {
  return useQuery({
    queryKey: ["dashboard", "categories", limit],
    queryFn: () =>
      apiClient.get<CategoryBreakdown[]>(
        `/dashboard/categories?limit=${limit}`,
      ),
    staleTime: 60_000,
  });
}

// ── v1.9 Stage 3.3 — library composition ──────────────────────

export interface CompositionRow {
  key: string;
  label: string;
  count: number;
  total_size_bytes: number;
}

export interface BitrateMatrixRow {
  library_id: string | null;
  library_name: string | null;
  resolution_key: string;
  video_codec: string | null;
  container: string | null;
  file_count: number;
  median_bitrate_kbps: number;
}

export interface Composition {
  resolutions: CompositionRow[];
  extensions: CompositionRow[];
  containers: CompositionRow[];
  subtitle_formats: CompositionRow[];
  subtitle_languages: CompositionRow[];
  audio_languages: CompositionRow[];
  unknown_tracks: { video_unknown_count: number; audio_unknown_count: number };
  subtitles_internal_external: { internal: number; external: number };
  orphan_count: number;
  bitrate_matrix: BitrateMatrixRow[];
}

/** v1.9 Stage 3.3 — the redesigned Categories card calls this in
 *  one shot instead of spraying nine GETs at the server. */
export function useDashboardComposition(libraryId: string | null = null) {
  const qs = libraryId
    ? `?library_id=${encodeURIComponent(libraryId)}`
    : "";
  return useQuery({
    queryKey: ["dashboard", "composition", libraryId],
    queryFn: () => apiClient.get<Composition>(`/dashboard/composition${qs}`),
    staleTime: 60_000,
  });
}

// ── v1.9 Stage 9.5.7 (OP-8 / OP-9) — language-preference surfaces ─

export interface ForeignAudioSummary {
  count: number;
  sample_ids: string[];
  preferred_audio_languages: string[];
  preferred_subtitle_languages: string[];
}

export interface IncompatibleMediaSummary {
  count: number;
  sample_ids: string[];
}

export function useDashboardForeignAudio() {
  return useQuery({
    queryKey: ["dashboard", "foreign-audio"],
    queryFn: () =>
      apiClient.get<ForeignAudioSummary>("/dashboard/foreign-audio"),
    staleTime: 60_000,
  });
}

export function useDashboardIncompatibleMedia() {
  return useQuery({
    queryKey: ["dashboard", "incompatible-media"],
    queryFn: () =>
      apiClient.get<IncompatibleMediaSummary>("/dashboard/incompatible-media"),
    staleTime: 60_000,
  });
}
