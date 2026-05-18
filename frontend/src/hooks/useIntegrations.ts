import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface IntegrationKind {
  kind: string;
  label: string;
  config_schema: {
    type?: string;
    required?: string[];
    properties?: Record<
      string,
      {
        type?: string;
        title?: string;
        description?: string;
        default?: unknown;
        minimum?: number;
        maximum?: number;
        /** Stage 11 (v1.7) — present on ``type: "array"``
         *  fields like ``source_whitelist`` describing the
         *  array element type. The dynamic input only reads
         *  the array's outer ``type`` to pick its renderer,
         *  but typing ``items`` lets test fixtures declare
         *  shape-accurate metadata. */
        items?: { type?: string };
      }
    >;
  };
  secret_fields: string[];
}

export interface Integration {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  poll_interval_seconds: number;
  config: Record<string, unknown>;
  health_status: "unknown" | "ok" | "degraded" | "error";
  health_detail: string | null;
  health_checked_at: string | null;
  created_at: string;
  updated_at: string;
  has_secrets: boolean;
}

/** Stage 19 (audit follow-up): one-time response from the
 *  webhook-secret generator endpoint. The plaintext is shown ONCE
 *  to the operator; the backend cannot return it again. */
export interface WebhookSecretResponse {
  integration_id: string;
  webhook_secret: string;
  webhook_url_suffix: string;
  instructions: string;
}

export interface IntegrationHealth {
  integration_id: string;
  status: "unknown" | "ok" | "degraded" | "error";
  detail: string | null;
  metadata: Record<string, unknown>;
}

export interface DiscoveredLibraryEntry {
  upstream_id: string;
  name: string;
  kind: string;
  root_path: string | null;
  metadata: Record<string, unknown>;
}

export interface IntegrationCreatePayload {
  name: string;
  kind: string;
  enabled?: boolean;
  poll_interval_seconds?: number;
  config: Record<string, unknown>;
  secrets: Record<string, unknown>;
}

export interface IntegrationUpdatePayload {
  name?: string;
  enabled?: boolean;
  poll_interval_seconds?: number;
  config?: Record<string, unknown>;
  secrets?: Record<string, unknown>;
}

// ── Hooks ─────────────────────────────────────────────────────
export function useIntegrationKinds() {
  return useQuery({
    queryKey: ["integrations", "kinds"],
    queryFn: () => apiClient.get<IntegrationKind[]>("/integrations/kinds"),
    staleTime: 60_000,
  });
}

export function useIntegrations() {
  return useQuery({
    queryKey: ["integrations", "list"],
    queryFn: () => apiClient.get<Integration[]>("/integrations"),
    staleTime: 15_000,
  });
}

// ── Stage 08 (v1.7) — transcode profile picker ────────────────
export interface TranscodeProfileSummary {
  id: string;
  name: string;
  description: string | null;
  metadata: Record<string, unknown>;
}

/** Stage 08 (v1.7) — fetch the provider-side transcode profiles
 *  for an integration. Used by the optimization profile editor's
 *  picker when ``routing_target !== "in_process"``. Returns ``[]``
 *  for providers that don't implement the listing surface
 *  (Jellyfin shim, future providers without hand-off support).
 *
 *  When ``integrationId`` is null / empty the query is disabled,
 *  so callers can pass the operator's picked id reactively. */
export function useIntegrationTranscodeProfiles(integrationId: string | null) {
  return useQuery({
    queryKey: ["integrations", integrationId, "transcode-profiles"],
    queryFn: () =>
      apiClient.get<TranscodeProfileSummary[]>(
        `/integrations/${integrationId}/transcode-profiles`,
      ),
    enabled: Boolean(integrationId),
    staleTime: 60_000,
  });
}

export function useCreateIntegration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: IntegrationCreatePayload) =>
      apiClient.post<Integration>("/integrations", body),
    onSuccess: () => invalidateRelated(qc, "integration"),
  });
}

export function useUpdateIntegration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: IntegrationUpdatePayload }) =>
      apiClient.patch<Integration>(`/integrations/${id}`, patch),
    onSuccess: () => invalidateRelated(qc, "integration"),
  });
}

export function useDeleteIntegration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/integrations/${id}`),
    onSuccess: () => invalidateRelated(qc, "integration"),
  });
}

export function useTriggerHealthcheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<IntegrationHealth>(`/integrations/${id}/healthcheck`, {}),
    onSuccess: () => invalidateRelated(qc, "integration"),
  });
}

/**
 * Preflight a candidate configuration against the upstream without saving.
 * Used by the "Test connection" button in the Connect dialog.
 */
export function useTestIntegration() {
  return useMutation({
    mutationFn: (body: IntegrationCreatePayload) =>
      apiClient.post<IntegrationHealth>("/integrations/test", body),
  });
}

export function useDiscoverLibraries() {
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.get<DiscoveredLibraryEntry[]>(`/integrations/${id}/libraries`),
  });
}

/** Stage 13 (audit follow-up): response shape from
 *  ``POST /integrations/{id}/sync-tags``. The backend already exists
 *  and returns ``{integration_id, inserted, removed, title_count,
 *  skipped_no_path}``. */
export interface SyncTagsReport {
  integration_id: string;
  inserted: number;
  removed: number;
  title_count: number;
  skipped_no_path: number;
}

/** Stage 13 (audit follow-up): manual tag-sync trigger. The backend
 *  endpoint is admin-only; the UI hides the button entirely for
 *  non-admin users so the 403 path is unreachable. Invalidates the
 *  media namespace so any visible Files page picks up the new tag
 *  rows on its next refetch. */
export function useSyncTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<SyncTagsReport>(
        `/integrations/${id}/sync-tags`,
        {},
      ),
    // Tag rows live in the media namespace — invalidating "media"
    // catches every Files-page query keyed under it.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["media"] });
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });
}

/** Stage 19 (audit follow-up): generate (or rotate) the webhook
 *  HMAC secret for an integration. The mutation returns the
 *  plaintext exactly once — the caller MUST surface it to the
 *  operator immediately, since the backend cannot return it again.
 *
 *  Rotating is intentionally destructive: any upstream still using
 *  the old secret will start failing signature verification until
 *  reconfigured. The dialog warns operators before triggering. */
export function useGenerateWebhookSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (integrationId: string) =>
      apiClient.post<WebhookSecretResponse>(
        `/integrations/${encodeURIComponent(integrationId)}/webhook-secret`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });
}

// ── Stage 10 (v1.7) — VirusTotal status ────────────────────────

/** Response shape from ``GET /api/v1/integrations/virustotal/status``.
 *  Surfaces the three-window quota state (addendum B.7) plus
 *  queue size + configuration state for the VT card on the
 *  Integrations page. */
export interface VirusTotalStatus {
  // Three-window quota state.
  minute_used: number;
  minute_cap: number;
  minute_remaining: number;
  day_used: number;
  day_cap: number;
  day_remaining: number;
  month_used: number;
  month_cap: number;
  month_remaining: number;
  // Plan §516 legacy aliases — what the original spec
  // mandated. ``quota_used_today === day_used``; kept here
  // for whatever downstream callers depend on the plan-spec
  // names.
  quota_used_today: number;
  quota_limit: number;
  // Queue + last-check timestamps.
  queue_size: number;
  last_check_at: string | null;
  // Configuration state.
  enabled: boolean;
  configured: boolean;
}

/** Poll the VT status endpoint. The card refreshes every 30s
 *  — quota counters tick at a human pace, and the queue size
 *  changes only on scan runs, so a tight poll cadence wastes
 *  requests for no operator-visible benefit. */
export function useVirustotalStatus() {
  return useQuery({
    queryKey: ["integrations", "virustotal", "status"],
    queryFn: () =>
      apiClient.get<VirusTotalStatus>("/integrations/virustotal/status"),
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}


// ── v1.9 Stage 7.1 — discovery hooks ───────────────────────────


export interface PathMappingSuggestion {
  from: string;
  to: string;
  confidence: "high" | "medium" | "low" | "none";
  library_id: string | null;
  library_name: string | null;
}

interface PathMappingDiscoverResponse {
  integration_id: string;
  kind: string;
  suggestions: PathMappingSuggestion[];
}

interface WebhookSource {
  ip: string;
  count: number;
}

interface WebhookSourcesResponse {
  integration_id: string;
  sources: WebhookSource[];
}

interface UpstreamTagsResponse {
  integration_id: string;
  kind: string;
  tags: string[];
}

/** v1.9 Stage 7.1 — returns a function that POSTs to the path-
 *  mapping discovery endpoint and yields the suggestion list.
 *  The hook itself doesn't fire HTTP on render — the operator
 *  triggers it via the Auto-discover button. */
export function makeDiscoverPathMappings(integrationId: string | undefined) {
  return async (): Promise<PathMappingSuggestion[]> => {
    if (!integrationId) return [];
    const body = await apiClient.post<PathMappingDiscoverResponse>(
      `/integrations/${encodeURIComponent(integrationId)}/discover-path-mappings`,
    );
    return body?.suggestions ?? [];
  };
}

/** v1.9 Stage 7.1 — webhook source IP discovery. Returns just
 *  the IP strings (chip editor doesn't surface counts inline;
 *  that's a future enhancement). */
export function makeDiscoverWebhookSources(integrationId: string | undefined) {
  return async (): Promise<string[]> => {
    if (!integrationId) return [];
    const body = await apiClient.post<WebhookSourcesResponse>(
      `/integrations/${encodeURIComponent(integrationId)}/discover-webhook-sources`,
    );
    return (body?.sources ?? []).map((s) => s.ip);
  };
}

/** v1.9 Stage 7.2 — upstream tag listing. Used by both
 *  tag_allowlist and tag_denylist editors to populate
 *  suggestions. Returns a sorted unique string list. */
export function makeDiscoverUpstreamTags(integrationId: string | undefined) {
  return async (): Promise<string[]> => {
    if (!integrationId) return [];
    const body = await apiClient.get<UpstreamTagsResponse>(
      `/integrations/${encodeURIComponent(integrationId)}/upstream-tags`,
    );
    return body?.tags ?? [];
  };
}
