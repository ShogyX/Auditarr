/**
 * v1.9 Stage 8.1 — system logs data hooks.
 *
 * The logs page polls ``GET /api/v1/system/logs`` and renders
 * the result table. Auto-tail mode bumps the poll interval and
 * passes ``since`` so each tick only fetches new records — keeps
 * UI traffic low when the operator is watching a quiet system.
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/services/apiClient";

export interface LogRecord {
  timestamp: string;
  level: "debug" | "info" | "warning" | "error" | "critical";
  logger: string;
  category: string | null;
  event: string;
  context: Record<string, string | number | boolean>;
}

export interface LogsResponse {
  records: LogRecord[];
  count: number;
  total_buffered: number;
  next_cursor: number | null;
  last_error_at: string | null;
  buffer_capacity: number;
}

export interface UseLogsParams {
  service?: string;
  level?: string;
  since?: string;
  limit?: number;
  /** When true, poll every 2s (auto-tail). When false, single
   *  fetch + manual refresh. */
  tail?: boolean;
}

export function useLogs(params: UseLogsParams) {
  const qs = new URLSearchParams();
  if (params.service && params.service !== "all")
    qs.set("service", params.service);
  if (params.level) qs.set("level", params.level);
  if (params.since) qs.set("since", params.since);
  if (params.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return useQuery({
    queryKey: ["system", "logs", params],
    queryFn: () =>
      apiClient.get<LogsResponse>(
        `/system/logs${query ? `?${query}` : ""}`,
      ),
    refetchInterval: params.tail ? 2_000 : false,
    // Don't keep stale data across filter changes — the table
    // would flash old rows for a beat while the new query is
    // in flight.
    staleTime: 0,
  });
}

/** v1.9 Stage 9 audit fix (LOG-6) — trigger an NDJSON download
 *  in the browser using a fetch+blob round trip.
 *
 *  Setting ``window.location.href`` strips the
 *  ``Authorization: Bearer`` header that ``apiClient`` adds to
 *  every fetch. The export endpoint is admin-gated, so a naive
 *  navigation produces a silent 401 and the operator gets
 *  nothing. We instead fetch with credentials via apiClient
 *  (which carries the bearer header), blob the response, and
 *  click a hidden anchor to trigger the save dialog. */
export async function downloadLogsNdjson(params: {
  service?: string;
  level?: string;
  since?: string;
}): Promise<void> {
  const qs = new URLSearchParams();
  if (params.service && params.service !== "all")
    qs.set("service", params.service);
  if (params.level) qs.set("level", params.level);
  if (params.since) qs.set("since", params.since);
  const path = `/system/logs/export${qs.toString() ? `?${qs}` : ""}`;
  // apiClient's get returns parsed JSON by default; for NDJSON
  // we want the raw text. The simplest path is to call the
  // underlying fetch directly with the bearer header.
  const tokens =
    JSON.parse(localStorage.getItem("auditarr.auth") || "null")?.state
      ?.tokens ?? null;
  const headers: HeadersInit = tokens?.accessToken
    ? { Authorization: `Bearer ${tokens.accessToken}` }
    : {};
  const response = await fetch(`/api/v1${path}`, { headers });
  if (!response.ok) {
    throw new Error(
      `Log export failed: ${response.status} ${response.statusText}`,
    );
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const filename =
    response.headers
      .get("content-disposition")
      ?.match(/filename="([^"]+)"/)?.[1] || "auditarr-logs.ndjson";
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Yield a tick so the browser starts the download before we
  // revoke the URL.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
