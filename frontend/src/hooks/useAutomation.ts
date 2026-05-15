import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface JobKind {
  key: string;
  label: string;
  description: string;
  args_schema: {
    type?: string;
    required?: string[];
    properties?: Record<
      string,
      {
        type?: string;
        title?: string;
        description?: string;
        default?: unknown;
        enum?: unknown[];
      }
    >;
  };
  required_args: string[];
  timeout_seconds: number;
}

export interface Schedule {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  job_kind: string;
  job_args: Record<string, unknown>;
  cron: Record<string, unknown>;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  timeout_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface JobRun {
  id: string;
  schedule_id: string | null;
  job_kind: string;
  job_args: Record<string, unknown>;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
  trigger: string;
}

export interface OptimizationItem {
  id: string;
  media_file_id: string;
  profile: string;
  status: string;
  queued_by_rule_id: string | null;
  queued_at: string;
  item_metadata: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleCreatePayload {
  name: string;
  description?: string;
  enabled?: boolean;
  job_kind: string;
  job_args?: Record<string, unknown>;
  cron?: Record<string, unknown>;
  timeout_seconds?: number;
}

// ── Hooks ─────────────────────────────────────────────────────
export function useJobKinds() {
  return useQuery({
    queryKey: ["automation", "jobs"],
    queryFn: () => apiClient.get<JobKind[]>("/automation/jobs"),
    staleTime: 60_000,
  });
}

export function useSchedules() {
  return useQuery({
    queryKey: ["automation", "schedules"],
    queryFn: () => apiClient.get<Schedule[]>("/automation/schedules"),
    staleTime: 15_000,
  });
}

export function useCreateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScheduleCreatePayload) =>
      apiClient.post<Schedule>("/automation/schedules", body),
    onSuccess: () => invalidateRelated(qc, "automation"),
  });
}

export function useUpdateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<ScheduleCreatePayload> & { enabled?: boolean };
    }) => apiClient.patch<Schedule>(`/automation/schedules/${id}`, patch),
    onSuccess: () => invalidateRelated(qc, "automation"),
  });
}

export function useDeleteSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/automation/schedules/${id}`),
    onSuccess: () => invalidateRelated(qc, "automation"),
  });
}

export function useRunSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.post<JobRun>(`/automation/schedules/${id}/run`, {}),
    onSuccess: () => invalidateRelated(qc, "automation"),
  });
}

export function useRunJobNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { job_kind: string; job_args: Record<string, unknown> }) =>
      apiClient.post<JobRun>("/automation/run", body),
    onSuccess: () => invalidateRelated(qc, "automation"),
  });
}

export function useJobRuns(filters?: {
  schedule_id?: string;
  job_kind?: string;
  status?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["automation", "runs", filters ?? {}],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters?.schedule_id) params.set("schedule_id", filters.schedule_id);
      if (filters?.job_kind) params.set("job_kind", filters.job_kind);
      if (filters?.status) params.set("status", filters.status);
      if (filters?.limit) params.set("limit", String(filters.limit));
      const qs = params.toString();
      return apiClient.get<JobRun[]>(`/automation/runs${qs ? `?${qs}` : ""}`);
    },
    staleTime: 5_000,
  });
}

export function useOptimizationQueue(status?: string) {
  return useQuery({
    queryKey: ["automation", "optimization-queue", status ?? "any"],
    queryFn: () => {
      const qs = status ? `?status=${status}` : "";
      return apiClient.get<OptimizationItem[]>(`/automation/optimization-queue${qs}`);
    },
    staleTime: 10_000,
  });
}
