import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/services/apiClient";
import type {
  HealthResponse,
  PluginSummary,
  SystemInfo,
  SystemVersion,
} from "@/types/api";

export const queryKeys = {
  systemInfo: ["system", "info"] as const,
  systemVersion: ["system", "version"] as const,
  health: ["health"] as const,
  plugins: ["plugins"] as const,
};

export function useSystemInfo() {
  return useQuery({
    queryKey: queryKeys.systemInfo,
    queryFn: () => apiClient.get<SystemInfo>("/system/info"),
    staleTime: 60_000,
  });
}

/**
 * Stage 5 (audit fix, Issue 11): GET /system/version.
 *
 * Lightweight probe the sidebar polls so the version chip stays
 * current after an in-place update. Distinct from useSystemInfo
 * because the backend explicitly carved out /version for this
 * exact use case — the full /info block carries platform/python/
 * websocket-clients metadata the sidebar has no use for.
 *
 * 5-minute staleTime is plenty: deploys are infrequent and a
 * refresh after the user navigates the route is enough to pick
 * up a new version. We don't poll on an interval — that would
 * keep the tab alive after the user is gone.
 */
export function useSystemVersion() {
  return useQuery({
    queryKey: queryKeys.systemVersion,
    queryFn: () => apiClient.get<SystemVersion>("/system/version"),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => apiClient.get<HealthResponse>("/health"),
    refetchInterval: 30_000,
  });
}

export function usePlugins() {
  return useQuery({
    queryKey: queryKeys.plugins,
    queryFn: () => apiClient.get<PluginSummary[]>("/plugins"),
  });
}

// ── Stage 20: operator-facing config view ────────────────────
// Mirror of GET /api/v1/system/config — a structured view of the
// env-driven runtime config (api/auth/storage/updater/plugins/
// housekeeping). All fields are read-only; editing requires
// changing the env file + restarting the service. The UI surfaces
// each section as a card in the Settings page.

export interface SystemConfigApi {
  host: string;
  port: number;
  api_prefix: string;
  api_version: string;
  allowed_origins: string[];
  ws_require_auth: boolean;
  log_level: string;
  log_format: string;
  env: string;
}

export interface SystemConfigAuth {
  access_token_ttl_minutes: number;
  refresh_token_ttl_days: number;
  rate_limit_attempts: number;
  rate_limit_window_seconds: number;
}

export interface SystemConfigStorage {
  database_url: string;
  database_pool_size: number;
  database_max_overflow: number;
  redis_url: string;
  queue_name: string;
  data_dir: string;
  plugin_dir: string;
  builtin_plugin_dir: string;
  docs_dir: string;
  frontend_dist: string | null;
}

export interface SystemConfigUpdater {
  feed_url: string;
  check_interval_minutes: number;
  install_mode: string;
  apply_sentinel: string;
  apply_status_path: string;
}

export interface SystemConfigPlugins {
  gallery_url: string;
}

export interface SystemConfigHousekeeping {
  delivery_retention_days: number;
  update_check_retention_days: number;
  rule_evaluation_retention_days: number;
  job_run_retention_days: number;
}

export interface SystemConfig {
  api: SystemConfigApi;
  auth: SystemConfigAuth;
  storage: SystemConfigStorage;
  updater: SystemConfigUpdater;
  plugins: SystemConfigPlugins;
  housekeeping: SystemConfigHousekeeping;
}

// Admin-only on the backend. Non-admin callers get 403; the Settings
// page hides the affected sections in that case. We disable retries
// so non-admin users don't keep hammering the endpoint on 403.
export function useSystemConfig() {
  return useQuery({
    queryKey: ["system", "config"] as const,
    queryFn: () => apiClient.get<SystemConfig>("/system/config"),
    staleTime: 1000 * 60 * 5,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

// ── Stage 14 (audit follow-up): housekeeping + docs reload + audit ──

/** Last housekeeping run record. ``null`` means no run has ever
 *  completed; the Settings page surfaces that as "Never run". */
export interface HousekeepingRunRecord {
  id: string;
  trigger: "manual" | "scheduled";
  started_at: string;
  finished_at: string | null;
  deliveries_deleted: number;
  update_checks_deleted: number;
  rule_evaluations_deleted: number;
  job_runs_deleted: number;
  error: string | null;
}

export interface HousekeepingRunReport {
  trigger: "manual";
  notification_deliveries: number;
  update_checks: number;
  rule_evaluations: number;
  job_runs: number;
  total: number;
}

/** Stage 14 (audit follow-up): poll the last-run record. Admin-
 *  gated server-side; the Settings page already hides the panel
 *  for non-admins so we don't retry on 403. */
export function useHousekeepingLastRun() {
  return useQuery({
    queryKey: ["system", "housekeeping", "last-run"] as const,
    queryFn: () =>
      apiClient.get<HousekeepingRunRecord | null>(
        "/system/housekeeping/last-run",
      ),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** Stage 14 (audit follow-up): admin-only manual housekeeping
 *  trigger. Invalidates the last-run query so the panel updates
 *  immediately after the call returns. */
export function useRunHousekeeping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient.post<HousekeepingRunReport>(
        "/system/housekeeping/run",
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "housekeeping"] });
    },
  });
}

/** Stage 14 (audit follow-up): admin-only docs index reload.
 *  Returns ``{count}`` per the existing backend endpoint shape. */
export interface DocsReloadResult {
  count: number;
}

export function useReloadDocs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<DocsReloadResult>("/docs/reload", {}),
    onSuccess: () => {
      // Invalidate every docs query so the help drawer and the
      // /help page pick up fresh pages.
      qc.invalidateQueries({ queryKey: ["docs"] });
    },
  });
}

// ── v1.9 Stage 2.6 — Factory reset ─────────────────────────────

export interface FactoryResetResult {
  tables_truncated: number;
  trash_purged: boolean;
}

/** Admin-only. Wipes the application back to a fresh-install
 *  state — every table except ``users``, ``audit_log``, and
 *  ``alembic_version`` is truncated, the trash directory is
 *  cleared, and an audit entry is written. The current admin
 *  user keeps their account so they can stay logged in.
 *
 *  ``confirm_phrase`` must be the exact string
 *  ``"reset auditarr"`` — wrong phrase returns 422. Caller is
 *  responsible for surfacing the typed-confirmation gate. */
export function useFactoryReset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (confirm_phrase: string) =>
      apiClient.post<FactoryResetResult>("/system/factory-reset", {
        confirm_phrase,
      }),
    onSuccess: () => {
      // Everything client-side could be stale; clear the whole
      // cache. The shell re-fetches on next mount.
      qc.clear();
    },
  });
}

/** Stage 14 (audit follow-up): audit log row shape. */
export interface AuditLogEntry {
  id: number;
  occurred_at: string;
  actor_id: string | null;
  actor_label: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip_address: string | null;
  request_id: string | null;
  metadata: Record<string, unknown> | null;
}

export interface AuditLogFilters {
  actor_id?: string;
  action?: string;
  since?: string;
  until?: string;
  before_id?: number;
  limit?: number;
}

/** Stage 14 (audit follow-up): paginated audit log fetch. The
 *  AuditLogPage uses this directly with a controlled ``before_id``
 *  cursor for its "Load more" button. */
export function useAuditLog(filters: AuditLogFilters = {}) {
  return useQuery({
    queryKey: ["audit", "log", filters] as const,
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.actor_id) params.set("actor_id", filters.actor_id);
      if (filters.action) params.set("action", filters.action);
      if (filters.since) params.set("since", filters.since);
      if (filters.until) params.set("until", filters.until);
      if (filters.before_id != null)
        params.set("before_id", String(filters.before_id));
      params.set("limit", String(filters.limit ?? 100));
      return apiClient.get<AuditLogEntry[]>(
        `/audit/log?${params.toString()}`,
      );
    },
    staleTime: 10_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
