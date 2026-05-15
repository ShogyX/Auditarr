import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { invalidateRelated, invalidateRelatedDeferred } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface Library {
  id: string;
  name: string;
  root_path: string;
  kind: "movies" | "tv" | "music" | "mixed";
  enabled: boolean;
  scan_interval_minutes: number;
  integration_link: Record<string, unknown> | null;
  last_scan_at: string | null;
  last_scan_status: string | null;
  last_scan_file_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface MatchedRuleSummary {
  rule_id: string;
  rule_name: string;
  severity: string;
}

export interface MediaFileSummary {
  id: string;
  library_id: string;
  path: string;
  relative_path: string;
  filename: string;
  extension: string;
  size_bytes: number;
  mtime: string;
  category: string;
  severity: string;
  severity_rank: number;
  container: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  width: number | null;
  height: number | null;
  has_subtitles: boolean;
  is_orphaned: boolean;
  // Stage 27: quarantine state. Surfaced in summaries so the Files
  // table can render a badge without a per-row detail fetch.
  quarantined?: boolean;
  // Stage 3 (audit follow-up): matched-rules chip strip. Present
  // only when the request enabled ``include_matched_rules``; the
  // server returns an empty array if no rules matched, but older
  // callers and endpoints with the join disabled may omit the
  // field entirely, so it's typed as optional.
  matched_rules?: MatchedRuleSummary[];
  // Stage 13 (audit follow-up): tag names attached to the row when
  // ``include_tags=true`` is on. Empty array (and absent for older
  // callers) when not requested. Casing preserved by the backend —
  // "4K" and "4k" are distinct values per the audit's guard rail.
  tags?: string[];
}

/** Stage 13 (audit follow-up): one row from the dedicated
 *  ``/media/{id}/tags`` endpoint. The drawer uses ``source`` to
 *  group chips into "From rules", "From Sonarr", etc. */
export interface MediaTag {
  id: number;
  name: string;
  source: string;
  created_at: string;
}

export interface MediaPage {
  items: MediaFileSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface ScanRun {
  id: string;
  library_id: string;
  mode: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  files_seen: number;
  files_added: number;
  files_updated: number;
  files_orphaned: number;
  probe_failures: number;
  error: string | null;
  created_at: string;
}

export interface MediaFileDetail extends MediaFileSummary {
  duration_seconds: number | null;
  bitrate_kbps: number | null;
  subtitle_codec: string | null;
  framerate: number | null;
  subtitle_languages: string[] | null;
  audio_languages: string[] | null;
  probe: Record<string, unknown> | null;
  probe_failed: boolean;
  probe_error: string | null;
  last_scan_id: string | null;
  seen_at: string;
  // Stage 27: audit fields. Only meaningful when ``quarantined: true``.
  quarantined_at?: string | null;
  quarantined_reason?: string | null;
  // Stage 19 (audit follow-up): content hash + VirusTotal result.
  // All four nullable; the drawer's Security section hides itself
  // when both hash + VT result are null.
  hash_sha256?: string | null;
  hash_computed_at?: string | null;
  virustotal_result?: VirusTotalResult | null;
  virustotal_checked_at?: string | null;
  created_at: string;
  updated_at: string;
}

/** Stage 19 (audit follow-up): persisted VirusTotal lookup result.
 *  Two shapes: ``status: "ok"`` carries the counter quartet;
 *  ``status: "not_found"`` is the negative-result sentinel so the
 *  drawer can say "Unknown to VirusTotal" rather than render
 *  nothing. */
export type VirusTotalResult =
  | {
      status: "ok";
      malicious: number;
      suspicious: number;
      harmless: number;
      undetected: number;
      permalink: string;
      checked_at: string;
    }
  | {
      status: "not_found";
      checked_at: string;
    };

export interface MediaEvaluation {
  media_file_id: string;
  rule_id: string;
  rule_name: string;
  rule_enabled: boolean;
  severity: string;
  severity_rank: number;
  actions_summary: Record<string, unknown>;
  evaluated_at: string;
}

export interface MediaFilters {
  library_id?: string;
  category?: string;
  severity?: string;
  extension?: string;
  is_orphaned?: boolean;
  // Stage 27: quarantine filters.
  // - quarantined: undefined → use server default (excludes quarantined)
  // - quarantined: true     → only quarantined
  // - quarantined: false    → only non-quarantined (explicit; same as default)
  // - include_quarantined: true → return both, regardless of quarantine state
  // The two flags are independent; quarantined wins when both are set.
  quarantined?: boolean;
  include_quarantined?: boolean;
  search?: string;
  // Stage 23: sortable column (whitelist enforced server-side; unknown
  // values fall back to the legacy severity-first order rather than
  // 422'ing). ``sort_dir`` is constrained to asc|desc.
  sort?: MediaSortKey;
  sort_dir?: "asc" | "desc";
  // Stage 31: codec / container filters. Both are
  // comma-separated strings (matching the existing severity
  // filter shape) — the server splits on commas. Single value
  // is also fine (no comma → IN clause of one).
  //
  // Values come from probed columns on media_files; the UI
  // sources its option list from /dashboard/categories so
  // operators only see codec/container values that actually
  // appear in their library.
  video_codec?: string;
  container?: string;
  // Stage 3 (audit follow-up): scope tri-state. Independent of
  // ``category`` (which still does exact equality when set).
  scope?: "all" | "media" | "non-media";
  // Stage 3 (audit follow-up): empty-severity-filter sentinel.
  // The Files scope bar lets operators toggle every severity chip
  // off; the page state then passes ``severities_empty: true`` so
  // the server returns zero rows instead of falling through to
  // "no filter ⇒ all rows".
  severities_empty?: boolean;
  // Stage 3 (audit follow-up): toggle the matched-rules join on
  // the list endpoint. Off by default for callers that don't need
  // it (dashboard summaries); the Files page turns it on so the
  // optional ``matched_rules`` column can render without per-row
  // detail fetches.
  include_matched_rules?: boolean;
  // Stage 13 (audit follow-up): toggle the tags join on the list
  // endpoint. Off by default. The Files page turns it on when the
  // optional ``tags`` column is enabled.
  include_tags?: boolean;
}

export type MediaSortKey =
  | "path"
  | "filename"
  | "size_bytes"
  | "mtime"
  | "severity_rank"
  | "category"
  | "extension"
  | "seen_at"
  // Stage 3 (audit follow-up): three new sortable keys. ``severity``
  // is the alias for ``severity_rank`` exposed under the column's
  // human label; ``video_codec`` and ``container`` are the two new
  // sortable probe columns.
  | "severity"
  | "video_codec"
  | "container";

export interface BulkReevaluateResult {
  files_evaluated: number;
  files_not_found: string[];
}

// ── Library hooks ─────────────────────────────────────────────
export function useLibraries() {
  return useQuery({
    queryKey: ["libraries"],
    queryFn: () => apiClient.get<Library[]>("/libraries"),
    staleTime: 30_000,
  });
}

export function useCreateLibrary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      root_path: string;
      kind?: string;
      scan_interval_minutes?: number;
    }) => apiClient.post<Library>("/libraries", body),
    onSuccess: () => invalidateRelated(qc, "library"),
  });
}

export function useUpdateLibrary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<
        Pick<Library, "name" | "root_path" | "kind" | "enabled" | "scan_interval_minutes">
      >;
    }) => apiClient.patch<Library>(`/libraries/${id}`, patch),
    onSuccess: () => invalidateRelated(qc, "library"),
  });
}

export function useDeleteLibrary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/libraries/${id}`),
    // Stage 5 (audit follow-up): the pre-Stage-5 ``onSuccess``
    // ran 8 ``invalidateQueries`` calls synchronously the moment
    // the API call returned, which on a heavy library produced a
    // visible UI freeze and frequently appeared as a crash to the
    // operator (audit issues #8, #9, #28). Two-part fix:
    //   1. ``onMutate`` removes the deleted library from the
    //      ``libraries`` cache immediately so the row disappears
    //      from the table before the network round-trip ends.
    //   2. ``onSettled`` uses the deferred-invalidation helper,
    //      which marks the related caches stale without firing
    //      eager refetches. Each downstream view refetches on its
    //      next mount/observation; no synchronous burst.
    onMutate: async (id: string) => {
      await qc.cancelQueries({ queryKey: ["libraries"] });
      const previous = qc.getQueryData<Library[]>(["libraries"]);
      if (previous) {
        qc.setQueryData<Library[]>(
          ["libraries"],
          previous.filter((lib) => lib.id !== id),
        );
      }
      return { previous };
    },
    onError: (_err, _id, ctx) => {
      // Roll back the optimistic removal.
      if (ctx?.previous) {
        qc.setQueryData(["libraries"], ctx.previous);
      }
      // The error toast comes from the caller's catch; here we
      // just make sure the cache is sane.
    },
    onSettled: () => {
      // Library deletion cascades to media files, scan runs, and any
      // notification channel / rule scoped to the library. The shared
      // ``invalidateRelatedDeferred`` helper knows the dependency graph
      // (see ``frontend/src/lib/invalidate.ts``) and marks every related
      // key stale without eager refetching.
      invalidateRelatedDeferred(qc, "library");
    },
  });
}

// ── Media hooks ───────────────────────────────────────────────
export function useMediaList(
  filters: MediaFilters & { offset?: number; limit?: number },
  options?: Partial<UseQueryOptions<MediaPage>>,
) {
  return useQuery({
    queryKey: ["media", "list", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(filters)) {
        if (v !== undefined && v !== "" && v !== null) {
          params.set(k, String(v));
        }
      }
      const qs = params.toString();
      return apiClient.get<MediaPage>(`/media${qs ? `?${qs}` : ""}`);
    },
    staleTime: 10_000,
    ...options,
  });
}

// ── Scan hooks ────────────────────────────────────────────────
export function useScanList(libraryId?: string) {
  return useQuery({
    queryKey: ["scans", "list", libraryId ?? null],
    queryFn: () => {
      const qs = libraryId ? `?library_id=${libraryId}` : "";
      return apiClient.get<ScanRun[]>(`/scans${qs}`);
    },
    staleTime: 10_000,
  });
}

/** Stage 14 (audit follow-up): per-scan detail. Backs the new
 *  ``/scans/:scanId`` route. Per the plan's guard rail, the
 *  snapshot does NOT auto-refetch on websocket events for completed
 *  scans — the read is intentionally a frozen artifact. We keep
 *  ``refetchOnWindowFocus: false`` and a long-ish staleTime so
 *  background refetches are rare. */
export function useScanDetail(scanId: string | null) {
  return useQuery({
    queryKey: ["scans", "detail", scanId] as const,
    queryFn: () =>
      apiClient.get<ScanRun>(`/scans/${encodeURIComponent(scanId!)}`),
    enabled: !!scanId,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      libraryId,
      mode = "full",
      followSymlinks = false,
    }: {
      libraryId: string;
      mode?: "full" | "incremental" | "targeted" | "rescan";
      followSymlinks?: boolean;
    }) =>
      apiClient.post<ScanRun>(`/scans/libraries/${libraryId}`, {
        mode,
        follow_symlinks: followSymlinks,
      }),
    // Stage 8 (audit follow-up): scan is now async-by-default
    // server-side. The response is the queued ScanRun row, not a
    // completed one — the UI watches WS events for progress.
    onSuccess: () => invalidateRelated(qc, "scan"),
  });
}

/**
 * Stage 8 (audit follow-up): enqueue a scan for every enabled
 * library at once. Backend ``POST /scans/all`` returns the list of
 * queued runs (or empty if there are no enabled libraries to scan).
 * Libraries already scanning are silently skipped.
 */
export function useTriggerScanAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      mode = "full",
      followSymlinks = false,
    }: {
      mode?: "full" | "incremental" | "targeted" | "rescan";
      followSymlinks?: boolean;
    }) =>
      apiClient.post<ScanRun[]>("/scans/all", {
        mode,
        follow_symlinks: followSymlinks,
      }),
    onSuccess: () => invalidateRelated(qc, "scan"),
  });
}

// ── Stage 23: detail, evaluations, bulk re-evaluate ──────────

/** Single media file with the full probe payload. Used by the
 *  Files page detail drawer. Cached briefly because the probe blob
 *  is large and we don't want every keystroke in the file list to
 *  re-fetch it; ``staleTime: 30_000`` matches the existing
 *  ``useLibraries`` cadence. */
export function useMediaDetail(mediaId: string | null) {
  return useQuery({
    queryKey: ["media", "detail", mediaId] as const,
    queryFn: () =>
      apiClient.get<MediaFileDetail>(`/media/${encodeURIComponent(mediaId!)}`),
    enabled: !!mediaId,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

/** Rule evaluations attached to one file. Enriched with rule names
 *  server-side so the drawer doesn't have to round-trip per row. */
export function useMediaEvaluations(mediaId: string | null) {
  return useQuery({
    queryKey: ["media", "evaluations", mediaId] as const,
    queryFn: () =>
      apiClient.get<MediaEvaluation[]>(
        `/media/${encodeURIComponent(mediaId!)}/evaluations`,
      ),
    enabled: !!mediaId,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

/** Stage 13 (audit follow-up): tags attached to one file with
 *  their full ``{id, name, source, created_at}`` shape. The drawer
 *  groups chips by ``source`` to render "From rules" / "From Sonarr"
 *  / "Manual" sections. ``staleTime`` matches the evaluations hook
 *  — tags change rarely once a file is indexed. */
export function useMediaTags(mediaId: string | null) {
  return useQuery({
    queryKey: ["media", "tags", mediaId] as const,
    queryFn: () =>
      apiClient.get<MediaTag[]>(
        `/media/${encodeURIComponent(mediaId!)}/tags`,
      ),
    enabled: !!mediaId,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

/** Stage 18 (audit follow-up): union of every distinct tag name
 *  across the library. Used by the visual rule builder's tag
 *  condition value-input and by the automation form's tag-scope
 *  chip-input. Cached aggressively (5min) because the universe of
 *  tag names is small and rarely changes; both consumers want
 *  responsive UI more than they want freshness. */
export function useTagsCatalog() {
  return useQuery({
    queryKey: ["tags", "catalog"] as const,
    queryFn: () => apiClient.get<string[]>("/tags"),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

/** Bulk re-evaluation against the enabled rule set.
 *
 *  Invalidates the media list (severity/rank may change), the
 *  detail of any open drawer, and per-file evaluation lists.
 *  The query-key invalidation patterns intentionally over-include
 *  rather than try to be surgical — re-evaluation is rare enough
 *  that a wide refresh is preferable to a stale UI lying about a
 *  file's current severity. */
export function useBulkReevaluate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaIds: string[]) =>
      apiClient.post<BulkReevaluateResult>("/media/bulk/reevaluate", {
        media_ids: mediaIds,
      }),
    onSuccess: () => invalidateRelated(qc, "media"),
  });
}

// ── Stage 27: reprobe + quarantine hooks ─────────────────────────
//
// Each mutation invalidates the media list (severity/probe state
// changed) and the per-file detail (drawer reflects the new state).
// The bulk variants share the same invalidation surface since the
// list query doesn't differentiate "you touched one file" from
// "you touched many".

export interface BulkReprobeResult {
  files_reprobed: number;
  files_failed: number;
  files_not_found: string[];
  files_orphaned: number;
}

export interface BulkQuarantineResult {
  files_quarantined: number;
  files_not_found: string[];
}

export interface BulkUnquarantineResult {
  files_unquarantined: number;
  files_not_found: string[];
}

export function useReprobeMedia() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaId: string) =>
      apiClient.post<MediaFileDetail>(`/media/${mediaId}/reprobe`),
    onSuccess: (_data, mediaId) => {
      invalidateRelated(qc, "media");
      qc.invalidateQueries({ queryKey: ["media", mediaId] });
    },
  });
}

export function useQuarantineMedia() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mediaId, reason }: { mediaId: string; reason?: string }) =>
      apiClient.post<MediaFileDetail>(`/media/${mediaId}/quarantine`, {
        reason: reason ?? null,
      }),
    onSuccess: (_data, { mediaId }) => {
      invalidateRelated(qc, "media");
      qc.invalidateQueries({ queryKey: ["media", mediaId] });
    },
  });
}

export function useUnquarantineMedia() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaId: string) =>
      apiClient.post<MediaFileDetail>(`/media/${mediaId}/unquarantine`),
    onSuccess: (_data, mediaId) => {
      invalidateRelated(qc, "media");
      qc.invalidateQueries({ queryKey: ["media", mediaId] });
    },
  });
}

export function useBulkReprobe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaIds: string[]) =>
      apiClient.post<BulkReprobeResult>("/media/bulk/reprobe", {
        media_ids: mediaIds,
      }),
    onSuccess: () => invalidateRelated(qc, "media"),
  });
}

export function useBulkQuarantine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      mediaIds,
      reason,
    }: {
      mediaIds: string[];
      reason?: string;
    }) =>
      apiClient.post<BulkQuarantineResult>("/media/bulk/quarantine", {
        media_ids: mediaIds,
        reason: reason ?? null,
      }),
    onSuccess: () => invalidateRelated(qc, "media"),
  });
}

export function useBulkUnquarantine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaIds: string[]) =>
      apiClient.post<BulkUnquarantineResult>("/media/bulk/unquarantine", {
        media_ids: mediaIds,
      }),
    onSuccess: () => invalidateRelated(qc, "media"),
  });
}
