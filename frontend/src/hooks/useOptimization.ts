import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface OptimizationProfile {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  settings: Record<string, unknown>;
  max_input_bytes: number | null;
  // Stage 7 (audit follow-up): routing target. NULL ⇒ in-process
  // ffmpeg runner. When set, the worker dispatches to the named
  // integration. UI is in OptimizationProfileDialog.
  optimization_integration_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface OptimizationItem {
  id: string;
  media_file_id: string;
  profile: string;
  status: string;
  queued_by_rule_id: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress_pct: number;
  original_size_bytes: number | null;
  optimized_size_bytes: number | null;
  backup_path: string | null;
  item_metadata: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileCreatePayload {
  name: string;
  description?: string;
  enabled?: boolean;
  settings: Record<string, unknown>;
  max_input_bytes?: number;
  optimization_integration_id?: string | null;
}

export interface WorkerReport {
  item_id: string | null;
  status: string;
  detail: string | null;
}

// ── Profile hooks ─────────────────────────────────────────────
export function useOptimizationProfiles() {
  return useQuery({
    queryKey: ["optimization", "profiles"],
    queryFn: () => apiClient.get<OptimizationProfile[]>("/optimization/profiles"),
    staleTime: 30_000,
  });
}

export function useCreateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileCreatePayload) =>
      apiClient.post<OptimizationProfile>("/optimization/profiles", body),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<ProfileCreatePayload> }) =>
      apiClient.patch<OptimizationProfile>(`/optimization/profiles/${id}`, patch),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

export function useDeleteProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/optimization/profiles/${id}`),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

// ── Queue hooks ───────────────────────────────────────────────
export function useOptimizationQueueDetail(filters?: { status?: string; limit?: number }) {
  return useQuery({
    queryKey: ["optimization", "queue", filters ?? {}],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters?.status) params.set("status", filters.status);
      if (filters?.limit) params.set("limit", String(filters.limit));
      const qs = params.toString();
      return apiClient.get<OptimizationItem[]>(`/optimization/queue${qs ? `?${qs}` : ""}`);
    },
    // Bug-hunt 1 (was: hard-coded 5_000ms). Poll fast (5s) only when
    // there's active work — running or queued items whose progress
    // the UI needs to surface. When everything is settled (completed,
    // failed, cancelled, skipped), stop polling so an idle
    // Optimization page doesn't hammer the API forever. React Query
    // accepts a function returning ``number | false``; ``false``
    // halts refetches until the query's data shape changes (e.g. a
    // mutation invalidates and refills).
    refetchInterval: (query) => {
      const data = query.state.data as OptimizationItem[] | undefined;
      if (!data) return 5_000; // initial load — keep trying
      const hasActive = data.some(
        (item) => item.status === "running" || item.status === "queued",
      );
      return hasActive ? 5_000 : false;
    },
  });
}

export function useEnqueueOptimization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { media_file_id: string; profile: string }) =>
      apiClient.post<OptimizationItem>("/optimization/enqueue", body),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

export function useRunNextOptimization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<WorkerReport>("/optimization/run-next", {}),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

export function useRunOptimizationItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.post<WorkerReport>(`/optimization/${id}/run`, {}),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

export function useCancelOptimizationItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.post<OptimizationItem>(`/optimization/${id}/cancel`, {}),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

export function useRetryOptimizationItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.post<OptimizationItem>(`/optimization/${id}/retry`, {}),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}

// ── Stage 28: bulk enqueue ───────────────────────────────────
//
// Closes the last Stage 23 ledger item — wires the previously
// disabled "Optimize" button in the Files selection bar to a
// real backend endpoint. The four-bucket response shape lets the
// UI surface a useful toast without lying about what happened.

export interface BulkEnqueueOptimizationResult {
  queued: number;
  already_queued: number;
  skipped_active: number;
  files_not_found: string[];
}

export function useBulkEnqueueOptimization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { media_ids: string[]; profile: string }) =>
      apiClient.post<BulkEnqueueOptimizationResult>(
        "/optimization/bulk-enqueue",
        body,
      ),
    onSuccess: () => invalidateRelated(qc, "optimization"),
  });
}
