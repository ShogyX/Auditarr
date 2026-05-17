/**
 * Playback insights query hooks (Stage 12 audit follow-up).
 *
 * Backs the new ``/api/v1/playback/*`` endpoints. Read endpoints
 * are cached at 60s for the live events feed (so a refresh during
 * triage is responsive) and 5 min for the stats aggregations (their
 * windows are days-wide so finer staleness adds nothing).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ───────────────────────────────────────────────────────
export interface PlaybackEvent {
  id: string;
  integration_id: string;
  integration_name: string | null;
  media_file_id: string | null;
  library_id: string | null;
  library_name: string | null;
  source_path: string;
  device_kind: string | null;
  device_name: string | null;
  decision: string;
  reason_code: string | null;
  source_codec: string | null;
  source_bitrate_kbps: number | null;
  source_width: number | null;
  source_height: number | null;
  source_container: string | null;
  target_codec: string | null;
  target_bitrate_kbps: number | null;
  started_at: string;
  completed_at: string | null;
  duration_s: number | null;
}

export interface PlaybackEventsPage {
  items: PlaybackEvent[];
  total: number;
  offset: number;
  limit: number;
}

export interface TopTranscodedFile {
  media_file_id: string | null;
  path: string;
  filename: string | null;
  transcode_count: number;
  last_transcoded_at: string | null;
  source_codec: string | null;
  target_codec: string | null;
}

export interface TopTranscodedResponse {
  items: TopTranscodedFile[];
  window_days: number;
}

export interface DeviceMatrixCell {
  device_kind: string;
  decision: string;
  count: number;
}

export interface DeviceMatrixResponse {
  cells: DeviceMatrixCell[];
  window_days: number;
}

export interface DecisionDayPoint {
  day: string; // ISO date "YYYY-MM-DD"
  decision: string;
  count: number;
}

export interface DecisionTrendResponse {
  points: DecisionDayPoint[];
  window_days: number;
}

export interface Cursor {
  id: string;
  integration_id: string;
  integration_name: string | null;
  integration_kind: string | null;
  cursor_kind: string;
  cursor_value: string;
  updated_at: string;
}

// ── Filters ────────────────────────────────────────────────────
export interface PlaybackEventFilters {
  /**
   * Only events for this media file. Used by ``FileDetailDrawer``'s
   * playback-history section. ``null`` disables the query so the
   * drawer can mount the hook unconditionally and let the row
   * decide whether to fetch.
   */
  mediaFileId?: string | null;
  libraryId?: string | null;
  integrationId?: string | null;
  decision?: string | null;
  deviceKind?: string | null;
  limit?: number;
  offset?: number;
}

// ── Hooks ──────────────────────────────────────────────────────
export function usePlaybackEvents(filters: PlaybackEventFilters = {}) {
  // Lift filters into the query key so changing any of them
  // re-fetches. ``null`` keys are normalized so the key is stable.
  const normalized = {
    mediaFileId: filters.mediaFileId ?? null,
    libraryId: filters.libraryId ?? null,
    integrationId: filters.integrationId ?? null,
    decision: filters.decision ?? null,
    deviceKind: filters.deviceKind ?? null,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
  };
  return useQuery({
    queryKey: ["playback", "events", normalized],
    // Stage 12: only auto-fire when a file id is bound. Other
    // callers (the dashboard card) explicitly opt-in via passing
    // the empty filters object, which has mediaFileId=null and
    // therefore stays disabled until they want it on.
    enabled: normalized.mediaFileId !== null,
    queryFn: () => {
      const params = new URLSearchParams();
      if (normalized.mediaFileId)
        params.set("media_file_id", normalized.mediaFileId);
      if (normalized.libraryId)
        params.set("library_id", normalized.libraryId);
      if (normalized.integrationId)
        params.set("integration_id", normalized.integrationId);
      if (normalized.decision)
        params.set("decision", normalized.decision);
      if (normalized.deviceKind)
        params.set("device_kind", normalized.deviceKind);
      params.set("limit", String(normalized.limit));
      params.set("offset", String(normalized.offset));
      return apiClient.get<PlaybackEventsPage>(
        `/playback/events?${params.toString()}`,
      );
    },
    staleTime: 60_000,
  });
}

export function useTopTranscoded(opts: { days?: number; limit?: number } = {}) {
  const days = opts.days ?? 30;
  const limit = opts.limit ?? 20;
  return useQuery({
    queryKey: ["playback", "top-transcoded", { days, limit }],
    queryFn: () =>
      apiClient.get<TopTranscodedResponse>(
        `/playback/stats/transcoded?days=${days}&limit=${limit}`,
      ),
    staleTime: 5 * 60_000,
  });
}

export function useDeviceMatrix(opts: { days?: number } = {}) {
  const days = opts.days ?? 30;
  return useQuery({
    queryKey: ["playback", "device-matrix", { days }],
    queryFn: () =>
      apiClient.get<DeviceMatrixResponse>(
        `/playback/stats/devices?days=${days}`,
      ),
    staleTime: 5 * 60_000,
  });
}

export function useDecisionTrend(opts: { days?: number } = {}) {
  const days = opts.days ?? 30;
  return useQuery({
    queryKey: ["playback", "decision-trend", { days }],
    queryFn: () =>
      apiClient.get<DecisionTrendResponse>(
        `/playback/stats/decisions?days=${days}`,
      ),
    staleTime: 5 * 60_000,
  });
}

export function useCursors() {
  return useQuery({
    queryKey: ["playback", "cursors"],
    queryFn: () => apiClient.get<Cursor[]>("/playback/cursors"),
    staleTime: 5 * 60_000,
  });
}

export function useResetCursors() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (integrationId: string) =>
      apiClient.post<void>(`/playback/cursors/${integrationId}/reset`, {}),
    // Cursor reset is a playback-namespace change — invalidate
    // both the cursors list and any dashboard panels that show
    // playback summaries.
    onSuccess: () => invalidateRelated(qc, "playback"),
  });
}

// ── Stage 09 (v1.7) — live playback ──────────────────────────

/** One in-progress playback session as returned by
 *  ``GET /playback/live``. The dashboard's "Live now" tile
 *  consumes a list of these. */
export interface LivePlaybackSession {
  integration_id: string;
  integration_name: string;
  integration_kind: string;
  upstream_id: string;
  source_path: string;
  decision: string;
  state: string;
  started_at: string;
  progress_pct: number | null;
  user: string | null;
  device_kind: string | null;
  device_name: string | null;
  source_codec: string | null;
  source_bitrate_kbps: number | null;
  source_width: number | null;
  source_height: number | null;
  source_container: string | null;
  target_codec: string | null;
  target_bitrate_kbps: number | null;
  title: string | null;
  /** When the post-remap path matches a known MediaFile, the
   *  row's id. ``null`` means path mappings haven't caught the
   *  file — frontend shows a path-mappings hint when any
   *  session has ``media_file_id=null`` (addendum A.7). */
  media_file_id: string | null;
}

export interface LivePlaybackResponse {
  sessions: LivePlaybackSession[];
  total: number;
  resolved: number;
  unresolved: number;
  polled_at: string;
}

/** Poll the live-playback aggregating endpoint.
 *
 *  Plan §487 — the dashboard's "Live now" tile reads this on
 *  a 15-second interval. ``refetchInterval`` keeps the tile
 *  fresh without operator interaction. The endpoint is cheap
 *  (one round-trip per enabled Plex/Jellyfin integration,
 *  bounded by the small per-server session counts).
 *
 *  Future enhancement: the backend's
 *  ``playback.live_changed`` WebSocket event invalidates this
 *  query for snappier updates between polls. */
export function useLivePlaybacks() {
  return useQuery({
    queryKey: ["playback", "live"],
    queryFn: () => apiClient.get<LivePlaybackResponse>("/playback/live"),
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
    // The session list changes on every play/pause/seek, so a
    // short stale time means cross-component remounts (e.g.
    // navigating away + back) re-fetch promptly.
    staleTime: 5_000,
  });
}
