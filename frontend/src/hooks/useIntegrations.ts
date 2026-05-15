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
