import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export type InstallMode = "docker" | "bare-metal" | "unmanaged";

export interface UpdaterStatus {
  installed_version: string;
  latest_version: string | null;
  has_update: boolean;
  last_checked_at: string | null;
  last_check_ok: boolean | null;
  last_check_detail: string | null;
  feed_url: string;
  apply_in_progress: boolean;
  // Stage 19: install-environment context.
  install_mode: InstallMode;
  apply_enabled: boolean;
}

export interface UpdateCheck {
  id: string;
  checked_at: string;
  ok: boolean;
  latest_version: string | null;
  changelog: string | null;
  detail: string | null;
  feed_url: string;
}

export interface UpdateApply {
  id: string;
  status: string;
  from_version: string | null;
  to_version: string;
  started_at: string;
  finished_at: string | null;
  triggered_by_user_id: string | null;
  detail: string | null;
  error: string | null;
}

// ── Hooks ─────────────────────────────────────────────────────
export function useUpdaterStatus() {
  return useQuery({
    queryKey: ["updater", "status"],
    queryFn: () => apiClient.get<UpdaterStatus>("/updater/status"),
    // Poll every 30s so the sidebar badge picks up new release activity.
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

export function useUpdateChecks(limit = 20) {
  return useQuery({
    queryKey: ["updater", "checks", limit],
    queryFn: () => apiClient.get<UpdateCheck[]>(`/updater/checks?limit=${limit}`),
    staleTime: 30_000,
  });
}

export function useUpdateApplies(limit = 20) {
  return useQuery({
    queryKey: ["updater", "applies", limit],
    queryFn: () => apiClient.get<UpdateApply[]>(`/updater/applies?limit=${limit}`),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}

export function useTriggerCheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<UpdateCheck>("/updater/check", {}),
    onSuccess: () => invalidateRelated(qc, "updater"),
  });
}

export function useRequestApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (to_version: string) =>
      apiClient.post<UpdateApply>("/updater/apply", { to_version }),
    onSuccess: () => invalidateRelated(qc, "updater"),
  });
}

export function useRollback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apply_id: string) =>
      apiClient.post<UpdateApply>(`/updater/applies/${apply_id}/rollback`, {}),
    onSuccess: () => invalidateRelated(qc, "updater"),
  });
}

// v1.9 Stage 1.2 — force-clear a stuck apply. The backend's
// status endpoint reaps stale rows automatically (default
// 30 min); this hook is the operator's manual lever for when
// they don't want to wait.
export function useForceClearApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apply_id: string) =>
      apiClient.post<UpdateApply>(`/updater/applies/${apply_id}/force-clear`, {}),
    onSuccess: () => invalidateRelated(qc, "updater"),
  });
}
