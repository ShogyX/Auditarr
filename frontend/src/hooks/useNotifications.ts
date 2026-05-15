import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface NotificationKind {
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
        enum?: unknown[];
      }
    >;
  };
  secret_fields: string[];
}

export interface NotificationChannel {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  config: Record<string, unknown>;
  min_severity_rank: number;
  last_delivery_status: string | null;
  last_delivery_at: string | null;
  last_delivery_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationDelivery {
  id: string;
  channel_id: string | null;
  channel_name: string;
  channel_kind: string;
  status: string;
  severity: string;
  subject: string;
  body: string;
  context: Record<string, unknown>;
  attempted_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  error: string | null;
}

export interface ChannelCreatePayload {
  name: string;
  kind: string;
  enabled?: boolean;
  config: Record<string, unknown>;
  secrets?: Record<string, string>;
  min_severity_rank?: number;
}

// ── Hooks ─────────────────────────────────────────────────────
export function useNotificationKinds() {
  return useQuery({
    queryKey: ["notifications", "kinds"],
    queryFn: () => apiClient.get<NotificationKind[]>("/notifications/kinds"),
    staleTime: 60_000,
  });
}

export function useNotificationChannels() {
  return useQuery({
    queryKey: ["notifications", "channels"],
    queryFn: () => apiClient.get<NotificationChannel[]>("/notifications"),
    staleTime: 15_000,
  });
}

export function useCreateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ChannelCreatePayload) =>
      apiClient.post<NotificationChannel>("/notifications", body),
    onSuccess: () => invalidateRelated(qc, "notification"),
  });
}

export function useUpdateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<ChannelCreatePayload> }) =>
      apiClient.patch<NotificationChannel>(`/notifications/${id}`, patch),
    onSuccess: () => invalidateRelated(qc, "notification"),
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/notifications/${id}`),
    onSuccess: () => invalidateRelated(qc, "notification"),
  });
}

export function useTestChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, severity, message }: { id: string; severity?: string; message?: string }) =>
      apiClient.post<NotificationDelivery>(`/notifications/${id}/test`, {
        severity: severity ?? "info",
        message,
      }),
    onSuccess: () => invalidateRelated(qc, "notification"),
  });
}

export function useNotificationDeliveries(filters?: {
  channel_id?: string;
  status?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["notifications", "deliveries", filters ?? {}],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters?.channel_id) params.set("channel_id", filters.channel_id);
      if (filters?.status) params.set("status", filters.status);
      if (filters?.limit) params.set("limit", String(filters.limit));
      const qs = params.toString();
      return apiClient.get<NotificationDelivery[]>(
        `/notifications/deliveries${qs ? `?${qs}` : ""}`,
      );
    },
    staleTime: 10_000,
  });
}
