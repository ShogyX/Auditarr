/**
 * Runtime settings + secrets + path-mappings client hooks (Stage 22).
 *
 * Backs the Settings page's three new operator panels. The shapes
 * mirror the backend payloads from
 * ``backend/app/api/v1/runtime_settings.py`` and
 * ``backend/app/api/v1/path_mappings.py``.
 *
 * Key design notes:
 *
 * - ``useRuntimeSettings()`` combines the schema (``describe``) with
 *   the current values (``list_effective``) into a single
 *   :type:`RuntimeField` array. The panel then renders directly from
 *   that — no per-component fetching, no juggling two response shapes.
 *
 * - Pattern constraints that look like enums
 *   (``^(debug|info|warning|error|critical)$``) are auto-converted to
 *   ``options`` arrays so the panel's <select> branch fires. The
 *   pattern itself is preserved for non-enum strings (URL, etc.).
 *
 * - Mutations invalidate exactly one query so re-renders stay
 *   surgical. The values query is the source of truth for "what is
 *   the app actually using right now"; we never optimistically lie
 *   about it.
 *
 * - Admin-only endpoints return 403 for non-admins. We disable retries
 *   so non-admin operators don't hammer them; the panel renders a
 *   friendly empty state if the query fails with 403.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { ApiError, apiClient } from "@/services/apiClient";
import { invalidateRelated } from "@/lib/invalidate";

// ── Types: shape returned by the backend ───────────────────────
// v1.10 — ``string_list`` for comma-separated lists like
// ``preferred_audio_languages``.
export type RuntimeFieldType =
  | "boolean"
  | "integer"
  | "number"
  | "string"
  | "string_list";
export type RuntimeImpact = "immediate" | "next_tick";
export type RuntimeSensitivity = "normal" | "elevated";

export interface RuntimeFieldDescribe {
  key: string;
  label: string;
  description: string;
  category: string;
  type: RuntimeFieldType;
  default: unknown;
  constraints: {
    ge?: number;
    le?: number;
    pattern?: string;
  };
  impact: RuntimeImpact;
  requires_warning: string | null;
  // Stage 2 additions. The backend started emitting these in
  // ``describe_runtime_settings()`` along with Stage 2. ``group`` is
  // ``null`` on fields that don't declare a sub-grouping. The other
  // two have sensible defaults so older backends still parse —
  // ``undefined`` falls through to the UI defaults below.
  group?: string | null;
  sensitivity?: RuntimeSensitivity;
  restart_required?: boolean;
}

export interface RuntimeFieldValue {
  value: unknown;
  is_override: boolean;
  env_default: unknown;
}

/** UI shape — describe + value, merged. This is what the panel reads. */
export interface RuntimeField {
  key: string;
  label: string;
  description: string;
  category: string;
  /** Stage 2: sub-grouping within a category, ``null`` for top-level. */
  group: string | null;
  type: RuntimeFieldType;
  default: unknown;
  /** Concrete options when the constraint pattern is an enum. */
  options: string[] | null;
  /** Raw constraints (range / pattern) — preserved for non-enum strings. */
  constraints: { ge?: number; le?: number; pattern?: string };
  impact: RuntimeImpact;
  /** Stage 2: ``"elevated"`` fields show an extra confirmation step. */
  sensitivity: RuntimeSensitivity;
  /** Stage 2: ``true`` fields show a "takes effect on restart" badge. */
  restart_required: boolean;
  requires_warning: string | null;
  value: unknown;
  is_override: boolean;
  env_default: unknown;
}

export interface RuntimeCategory {
  key: string;
  label: string;
}

// ── Pattern → options coercion ─────────────────────────────────
// The backend pins enum-shaped values as regex patterns (e.g.
// ``^(debug|info|warning|error|critical)$``). Build the options
// list client-side so the panel can render a <select> instead of a
// free-text input — much friendlier UX for these.
const ENUM_PATTERN = /^\^?\((?<opts>[A-Za-z0-9_\-\s|]+)\)\$?$/;

function patternToOptions(pattern: string | undefined): string[] | null {
  if (!pattern) return null;
  const m = ENUM_PATTERN.exec(pattern);
  if (!m?.groups?.opts) return null;
  const opts = m.groups.opts.split("|").map((s) => s.trim());
  if (opts.length < 2) return null;
  return opts;
}

const CATEGORY_LABELS: Record<string, string> = {
  logging: "Logging",
  auth: "Auth",
  rate_limiting: "Rate limiting",
  scanner: "Scanner",
  updater: "Updater",
  plugins: "Plugins",
  housekeeping: "Housekeeping",
  webhooks: "Webhooks",
  integrations: "Integrations",
};

function categoryLabel(key: string): string {
  return (
    CATEGORY_LABELS[key] ??
    key
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

// ── Query keys ─────────────────────────────────────────────────
export const runtimeKeys = {
  describe: ["runtime-settings", "describe"] as const,
  values: ["runtime-settings", "values"] as const,
  secrets: ["runtime-settings", "secrets"] as const,
  secretsDescribe: ["runtime-settings", "secrets", "describe"] as const,
  pathMappings: ["runtime-settings", "path-mappings"] as const,
  /** Stage 2: per-key history. The query is keyed on the setting key
   *  + limit so two drawers for different keys don't collide and
   *  bumping the limit triggers a fresh fetch. */
  history: (key: string, limit: number) =>
    ["runtime-settings", "history", key, limit] as const,
};

// ── Runtime settings: fields + categories ──────────────────────
export interface UseRuntimeSettingsResult {
  fields: RuntimeField[];
  categories: RuntimeCategory[];
  isLoading: boolean;
  isError: boolean;
  /** True specifically when the API returned 403. The panel renders an
   *  admin-only empty state in this case rather than a generic error. */
  isForbidden: boolean;
  /** Triggers a refetch of both describe + values. */
  refetch: () => Promise<void>;
}

export function useRuntimeSettings(): UseRuntimeSettingsResult {
  const describeQ = useQuery({
    queryKey: runtimeKeys.describe,
    queryFn: () =>
      apiClient.get<{ fields: RuntimeFieldDescribe[] | null }>(
        "/system/runtime-settings/describe",
      ),
    staleTime: 1000 * 60 * 5,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const valuesQ = useQuery({
    queryKey: runtimeKeys.values,
    queryFn: () =>
      apiClient.get<Record<string, RuntimeFieldValue> | null>(
        "/system/runtime-settings",
      ),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const describeData = describeQ.data?.fields ?? [];
  const valuesData = valuesQ.data ?? {};

  const fields: RuntimeField[] = describeData.map((d) => {
    const v = valuesData[d.key];
    return {
      key: d.key,
      label: d.label,
      description: d.description,
      category: d.category,
      // Stage 2: fall through to sensible defaults so the UI parses
      // payloads from older backends that don't emit these yet.
      group: d.group ?? null,
      sensitivity: d.sensitivity ?? "normal",
      restart_required: d.restart_required ?? false,
      type: d.type,
      default: d.default,
      options: patternToOptions(d.constraints?.pattern),
      constraints: d.constraints ?? {},
      impact: d.impact,
      requires_warning: d.requires_warning,
      value: v?.value ?? d.default,
      is_override: v?.is_override ?? false,
      env_default: v?.env_default ?? d.default,
    };
  });

  // Preserve first-seen category order from the describe payload.
  const seen = new Set<string>();
  const categories: RuntimeCategory[] = [];
  for (const f of fields) {
    if (!seen.has(f.category)) {
      seen.add(f.category);
      categories.push({ key: f.category, label: categoryLabel(f.category) });
    }
  }

  const isForbidden = isForbiddenError(describeQ.error) || isForbiddenError(valuesQ.error);

  return {
    fields,
    categories,
    isLoading: describeQ.isLoading || valuesQ.isLoading,
    isError: describeQ.isError || valuesQ.isError,
    isForbidden,
    refetch: async () => {
      await Promise.all([describeQ.refetch(), valuesQ.refetch()]);
    },
  };
}

function isForbiddenError(err: unknown): boolean {
  // Structural check rather than ``instanceof ApiError`` so it survives
  // duck-typed errors in tests, vi.mock module-replacement, and any
  // future refactor that swaps the error class. The contract is just
  // "object with status === 403".
  return isApiErrorWithStatus(err, 403);
}

/** Generic structural check: an object with the given numeric status. */
function isApiErrorWithStatus(err: unknown, status: number): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { status?: unknown }).status === status
  );
}

/** Best-effort message extraction from a thrown value. */
function extractMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  if (typeof err === "object" && err !== null) {
    const m = (err as { message?: unknown }).message;
    if (typeof m === "string") return m;
  }
  return "Unknown error";
}

// ── Mutations: set / clear override ────────────────────────────
export function useSetRuntimeOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { key: string; value: unknown }) =>
      apiClient.put<{ key: string; value: unknown; is_override: boolean }>(
        `/system/runtime-settings/${encodeURIComponent(vars.key)}`,
        { value: vars.value },
      ),
    onSuccess: (_data, vars) => {
      invalidateRelated(qc, "runtime-setting");
      // Stage 2: any open history drawer for this key needs the new
      // entry. We invalidate any matching prefix without caring about
      // the limit param.
      qc.invalidateQueries({
        queryKey: ["runtime-settings", "history", vars.key],
      });
    },
  });
}

export function useClearRuntimeOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      apiClient.delete<void>(
        `/system/runtime-settings/${encodeURIComponent(key)}`,
      ),
    onSuccess: (_data, key) => {
      invalidateRelated(qc, "runtime-setting");
      qc.invalidateQueries({
        queryKey: ["runtime-settings", "history", key],
      });
    },
  });
}

// ── Secrets ────────────────────────────────────────────────────
export interface SecretDescribe {
  key: string;
  label: string;
  description: string;
  category: string;
  min_length: number;
  max_length: number;
  has_test_handler: boolean;
}

export interface SecretStatus {
  key: string;
  label: string;
  category: string;
  has_value: boolean;
  last_set_at: string | null;
  set_by_user_id: string | null;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  last_test_detail: string | null;
}

/** Combined describe + status — the panel renders from this single shape. */
export interface SecretRow extends SecretStatus {
  description: string;
  min_length: number;
  max_length: number;
  has_test_handler: boolean;
}

export interface UseSecretsResult {
  secrets: SecretRow[];
  isLoading: boolean;
  isError: boolean;
  isForbidden: boolean;
  refetch: () => Promise<void>;
}

export function useSecrets(): UseSecretsResult {
  const describeQ = useQuery({
    queryKey: runtimeKeys.secretsDescribe,
    queryFn: () =>
      apiClient.get<{ secrets: SecretDescribe[] | null }>(
        "/system/secrets/describe",
      ),
    staleTime: 1000 * 60 * 5,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const statusQ = useQuery({
    queryKey: runtimeKeys.secrets,
    queryFn: () =>
      apiClient.get<{ secrets: SecretStatus[] | null }>("/system/secrets"),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const describeData = describeQ.data?.secrets ?? [];
  const statusByKey = new Map(
    (statusQ.data?.secrets ?? []).map((s) => [s.key, s] as const),
  );

  const secrets: SecretRow[] = describeData.map((d) => {
    const s = statusByKey.get(d.key);
    return {
      key: d.key,
      label: d.label,
      category: d.category,
      description: d.description,
      min_length: d.min_length,
      max_length: d.max_length,
      has_test_handler: d.has_test_handler,
      has_value: s?.has_value ?? false,
      last_set_at: s?.last_set_at ?? null,
      set_by_user_id: s?.set_by_user_id ?? null,
      last_tested_at: s?.last_tested_at ?? null,
      last_test_ok: s?.last_test_ok ?? null,
      last_test_detail: s?.last_test_detail ?? null,
    };
  });

  return {
    secrets,
    isLoading: describeQ.isLoading || statusQ.isLoading,
    isError: describeQ.isError || statusQ.isError,
    isForbidden: isForbiddenError(describeQ.error) || isForbiddenError(statusQ.error),
    refetch: async () => {
      await Promise.all([describeQ.refetch(), statusQ.refetch()]);
    },
  };
}

export function useSetSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { key: string; plaintext: string }) =>
      apiClient.put<void>(
        `/system/secrets/${encodeURIComponent(vars.key)}`,
        { plaintext: vars.plaintext },
      ),
    onSuccess: () => invalidateRelated(qc, "secret"),
  });
}

export function useClearSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      apiClient.delete<void>(`/system/secrets/${encodeURIComponent(key)}`),
    onSuccess: () => invalidateRelated(qc, "secret"),
  });
}

export interface SecretTestResult {
  ok: boolean;
  detail: string;
}

export function useTestSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (key: string): Promise<SecretTestResult> => {
      try {
        const r = await apiClient.post<{ ok: boolean; detail: string }>(
          `/system/secrets/${encodeURIComponent(key)}/test`,
        );
        return { ok: !!r.ok, detail: r.detail ?? "ok" };
      } catch (err) {
        // The backend returns 502 with code "integration_error" when
        // the upstream API rejects the secret. We surface that as
        // "ok: false" rather than letting the mutation throw, because
        // the test failing is a normal UX outcome (operator typo'd
        // the key) — not an exceptional error. Other errors (no
        // secret stored, 422) still throw.
        if (isApiErrorWithStatus(err, 502)) {
          return { ok: false, detail: extractMessage(err) };
        }
        throw err;
      }
    },
    // Either way, the test outcome gets persisted server-side, so
    // refresh the status list.
    onSettled: () => invalidateRelated(qc, "secret"),
  });
}

// ── Path mappings ──────────────────────────────────────────────
export interface PathMapping {
  from: string;
  to: string;
}

/** Stage 17 (audit follow-up): one entry in an integration's
 *  ``discovered_paths`` snapshot — the gap-detector source. */
export interface DiscoveredPath {
  library_id: string;
  label: string;
  upstream_path: string;
  discovered_at: string;
}

export interface PathMappingsIntegration {
  integration_id: string;
  name: string;
  kind: string;
  is_active: boolean;
  mappings: PathMapping[];
  raw: unknown[];
  /** Stage 17 (audit follow-up): snapshot of libraries discovered
   *  from the upstream at integration-create time (or via the
   *  manual rediscover endpoint). ``null`` means "never discovered"
   *  — the panel surfaces this as a "Discover now" admin button. */
  discovered_paths: DiscoveredPath[] | null;
}

export function usePathMappings(): UseQueryResult<{
  integrations: PathMappingsIntegration[];
}> {
  return useQuery({
    queryKey: runtimeKeys.pathMappings,
    queryFn: () =>
      apiClient.get<{ integrations: PathMappingsIntegration[] }>(
        "/system/path-mappings",
      ),
    refetchOnWindowFocus: false,
    // Path mappings read is non-admin-visible, so we don't need to
    // disable retries the way we do for admin-only queries.
  });
}

export function useUpdatePathMappings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { integrationId: string; mappings: PathMapping[] }) =>
      apiClient.put<{ integration_id: string; mappings: PathMapping[] }>(
        `/system/path-mappings/${encodeURIComponent(vars.integrationId)}`,
        { mappings: vars.mappings },
      ),
    onSuccess: () => invalidateRelated(qc, "path-mapping"),
  });
}

/** Stage 17 (audit follow-up): admin trigger to refresh the
 *  discovered-paths snapshot for an integration. Used by the
 *  "Discover now" button on integrations whose snapshot is missing
 *  (e.g. created before Stage 17) or stale. */
export function useRediscoverPaths() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (integrationId: string) =>
      apiClient.post<{
        integration_id: string;
        discovered_paths: DiscoveredPath[];
      }>(
        `/integrations/${encodeURIComponent(integrationId)}/discover-paths`,
        {},
      ),
    // Refresh path-mappings query so the new snapshot renders.
    onSuccess: () => invalidateRelated(qc, "path-mapping"),
  });
}

// ── Global path mappings (Stage 5 audit follow-up) ─────────────
export interface GlobalPathMappingRow {
  id: string;
  from_path: string;
  to_path: string;
  enabled: boolean;
  priority: number;
}

const globalPathMappingsKey = [
  "runtime-settings",
  "path-mappings",
  "global",
] as const;

export function useGlobalPathMappings(): UseQueryResult<
  GlobalPathMappingRow[]
> {
  return useQuery({
    queryKey: globalPathMappingsKey,
    queryFn: () =>
      apiClient.get<GlobalPathMappingRow[]>(
        "/system/path-mappings/global",
      ),
    refetchOnWindowFocus: false,
  });
}

export function useCreateGlobalPathMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      from_path: string;
      to_path: string;
      enabled?: boolean;
      priority?: number;
    }) =>
      apiClient.post<GlobalPathMappingRow>(
        "/system/path-mappings/global",
        body,
      ),
    onSuccess: () => invalidateRelated(qc, "path-mapping"),
  });
}

export function useUpdateGlobalPathMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      id: string;
      patch: Partial<Omit<GlobalPathMappingRow, "id">>;
    }) =>
      apiClient.patch<GlobalPathMappingRow>(
        `/system/path-mappings/global/${encodeURIComponent(vars.id)}`,
        vars.patch,
      ),
    onSuccess: () => invalidateRelated(qc, "path-mapping"),
  });
}

export function useDeleteGlobalPathMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.delete(
        `/system/path-mappings/global/${encodeURIComponent(id)}`,
      ),
    onSuccess: () => invalidateRelated(qc, "path-mapping"),
  });
}

// ── Path suggestions (Stage 5 audit follow-up) ────────────────
export interface PathSuggestions {
  library_roots: string[];
  integration_paths: { from: string; to: string }[];
  global_paths: string[];
}

export function usePathSuggestions(): UseQueryResult<PathSuggestions> {
  return useQuery({
    queryKey: ["runtime-settings", "path-suggestions"],
    queryFn: () =>
      apiClient.get<PathSuggestions>("/system/path-suggestions"),
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

// ── Runtime settings change history (Stage 2) ──────────────────
export interface RuntimeSettingChangeRow {
  id: number;
  key: string;
  prev_value: unknown;
  next_value: unknown;
  set_by_user_id: string | null;
  set_at: string;
}

export interface UseRuntimeSettingHistoryResult {
  changes: RuntimeSettingChangeRow[];
  isLoading: boolean;
  isError: boolean;
  isForbidden: boolean;
  refetch: () => Promise<void>;
}

/**
 * Stage 2: fetch the recent change log for a single runtime setting.
 *
 * Backs the per-field history drawer. The query is enabled only when
 * a key is provided so the panel can mount the hook unconditionally
 * (React rules-of-hooks) and pass ``null`` when the drawer is closed
 * — no network traffic happens until the operator opens a drawer.
 */
export function useRuntimeSettingHistory(
  key: string | null,
  limit = 50,
): UseRuntimeSettingHistoryResult {
  const q = useQuery({
    queryKey: key ? runtimeKeys.history(key, limit) : ["runtime-settings", "history", "disabled"],
    queryFn: () =>
      apiClient.get<{ changes: RuntimeSettingChangeRow[] | null }>(
        `/system/runtime-settings/${encodeURIComponent(key!)}/history?limit=${limit}`,
      ),
    enabled: !!key,
    refetchOnWindowFocus: false,
    retry: false,
  });

  return {
    changes: q.data?.changes ?? [],
    isLoading: q.isLoading && !!key,
    isError: q.isError,
    isForbidden: isForbiddenError(q.error),
    refetch: async () => {
      await q.refetch();
    },
  };
}

// ── Re-export ApiError for callers building error UX ───────────
export { ApiError };
