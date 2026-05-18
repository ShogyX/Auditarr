import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { invalidateRelated, invalidateRelatedDeferred } from "@/lib/invalidate";
import { toast } from "@/lib/toast";
import { ApiError, apiClient } from "@/services/apiClient";

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
  // Stage 27 added a ``quarantined`` flag here. Stage 05 (v1.7)
  // removed it along with the rest of the quarantine workflow
  // (Section A.0 — "delete means delete"). A file is either in
  // the library or it has been deleted by a rule (audit-logged
  // and moved to ``data_dir/trash/``); no intermediate state.
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
  // Stage 27 added ``quarantined_at`` and ``quarantined_reason``
  // here. Stage 05 (v1.7) removed them — see ``MediaFileSummary``.
  // Stage 19 (audit follow-up): content hash + VirusTotal result.
  // All four nullable; the drawer's Security section hides itself
  // when both hash + VT result are null.
  hash_sha256?: string | null;
  hash_computed_at?: string | null;
  virustotal_result?: VirusTotalResult | null;
  virustotal_checked_at?: string | null;
  /**
   * Stage 06 (v1.7) — VirusTotal scan status as a denormalised
   * column. One of ``"clean" | "malicious" | "suspicious" |
   * "not_found" | "error"`` (per ``VT_STATUS_VALUES`` in the
   * backend) or ``null`` for "never looked up". The Stage 06
   * built-in "VirusTotal non-clean" rule matches on this column;
   * the Stage 10 VT plugin will populate it. The drawer can
   * surface the value once Stage 10 lands.
   */
  vt_status?:
    | "clean"
    | "malicious"
    | "suspicious"
    | "not_found"
    | "error"
    | null;
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
  // Stage 27 carried ``quarantined`` and ``include_quarantined``
  // filter flags here. Stage 05 (v1.7) removed both alongside the
  // quarantine workflow (Section A.0 — "delete means delete").
  // Callers that used to omit both flags got "exclude quarantined"
  // for free; that behaviour is still the default outcome now
  // simply because no row carries a quarantined state any more.
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
  // Stage 02 — per-column quick filters.
  //
  // The Files-page toolbar adds an optional filter row beneath the
  // header. Each input writes to one of these fields. The server
  // accepts them as conventional substring/equality filters; the
  // existing ``search`` predicate covers a different surface
  // (full-path substring across the whole row), so these are
  // additive rather than alternatives.
  //
  // ``path_contains`` is a case-insensitive substring filter on
  // ``path`` (which carries the full file path). ``codec_contains``
  // is a case-insensitive substring filter on ``video_codec`` —
  // useful when an operator types ``hev`` to find both ``hevc``
  // and ``hevc-something``. ``container_eq`` and ``extension_eq``
  // are strict equality because container and extension are short
  // closed-set values where substring matches are noise.
  //
  // ``size_min``/``size_max`` and ``mtime_after``/``mtime_before``
  // are reserved for future UI (size and updated-time columns).
  // The contract is shipped now so the backend can be tested
  // end-to-end without a frontend follow-up.
  path_contains?: string;
  codec_contains?: string;
  container_eq?: string;
  extension_eq?: string;
  size_min?: number;
  size_max?: number;
  /** ISO 8601 timestamp string. */
  mtime_after?: string;
  /** ISO 8601 timestamp string. */
  mtime_before?: string;
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
    //
    // v1.8.1: toast on both success and error so the operator
    // always knows whether the click did anything. Pre-1.8.1
    // there was zero feedback on failure — the button just
    // stopped being "Scanning…" and the user was left guessing.
    onSuccess: (run) => {
      // The row is returned in whatever state the backend got
      // it to — usually "queued" but possibly "failed" if the
      // enqueue collided or Redis was down.
      if (run.status === "failed") {
        toast(
          run.error
            ? `Scan didn't start: ${run.error}`
            : "Scan didn't start. Check the worker logs.",
          "error",
          8000,
        );
      } else {
        toast("Scan queued", "ok");
      }
      invalidateRelated(qc, "scan");
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          // Library has a stuck queued/running scan. The
          // FilesPage error banner exposes a "Reset" button
          // calling useResetLibraryScans. Toast still fires
          // so the user knows the click registered.
          toast(
            "A scan is already running for this library. " +
              "Use 'Unstick library' to clear it if it's stuck.",
            "warn",
            6000,
          );
        } else if (err.status === 403) {
          toast("You need admin permission to run scans.", "error");
        } else {
          toast(`Scan failed to start: ${err.message}`, "error", 6000);
        }
      } else {
        toast(
          `Scan failed to start: ${(err as Error)?.message ?? "Unknown error"}`,
          "error",
          6000,
        );
      }
    },
  });
}

/**
 * Stage 8 (audit follow-up): enqueue a scan for every enabled
 * library at once. Backend ``POST /scans/all`` returns the list of
 * queued runs (or empty if there are no enabled libraries to scan).
 * Libraries already scanning are silently skipped.
 *
 * v1.8.1: success and error toasts. When any of the queued rows
 * comes back in a ``failed`` state (enqueue collision), surface
 * a warn-level toast naming the count.
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
    onSuccess: (runs) => {
      const failed = runs.filter((r) => r.status === "failed").length;
      const queued = runs.length - failed;
      if (queued === 0 && failed === 0) {
        toast(
          "No libraries to scan — all are either disabled or " +
            "already scanning.",
          "warn",
          5000,
        );
      } else if (failed > 0) {
        toast(
          `${queued} scan(s) queued; ${failed} failed to enqueue. ` +
            "Check the worker logs for details.",
          "warn",
          6000,
        );
      } else {
        toast(`${queued} scan(s) queued`, "ok");
      }
      invalidateRelated(qc, "scan");
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 403) {
          toast("You need admin permission to run scans.", "error");
        } else {
          toast(`Scan-all failed: ${err.message}`, "error", 6000);
        }
      } else {
        toast(
          `Scan-all failed: ${(err as Error)?.message ?? "Unknown error"}`,
          "error",
          6000,
        );
      }
    },
  });
}

/**
 * v1.8.1: ``POST /scans/libraries/{id}/reset`` — admin-only
 * endpoint that forcibly marks any ``queued``/``running``
 * ScanRun rows for the library as ``failed``. Lets the operator
 * unstick a library after a worker crash without waiting the
 * full 1-hour reaper threshold.
 *
 * The FilesPage exposes this as an "Unstick library" button
 * surfaced when a scan trigger comes back 409.
 */
export function useResetLibraryScans() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (libraryId: string) =>
      apiClient.post<{ reset_count: number; run_ids: string[] }>(
        `/scans/libraries/${libraryId}/reset`,
        {},
      ),
    onSuccess: (data) => {
      if (data.reset_count === 0) {
        toast(
          "Nothing to reset — no scans were stuck for this library.",
          "info",
        );
      } else {
        toast(
          `Reset ${data.reset_count} stuck scan(s). ` +
            "You can run a new scan now.",
          "ok",
          5000,
        );
      }
      invalidateRelated(qc, "scan");
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 403) {
          toast(
            "You need admin permission to reset scans.",
            "error",
          );
        } else if (err.status === 404) {
          toast("Library not found.", "error");
        } else {
          toast(`Reset failed: ${err.message}`, "error", 6000);
        }
      } else {
        toast(
          `Reset failed: ${(err as Error)?.message ?? "Unknown error"}`,
          "error",
          6000,
        );
      }
    },
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

/** Stage 15 (plan §656) — vocabulary endpoint. The distinct
 *  values currently in the library, used to drive value-pickers
 *  in the rule builder, optimization profile dialog, and
 *  automation editor. Backend caches the result for 60s, so a
 *  React-Query staleTime of 60s matches the backend cache TTL
 *  (matching staleTime to the server cache means we don't
 *  emit a request the server would have served from cache
 *  anyway). */
export interface MediaVocabulary {
  video_codecs: string[];
  audio_codecs: string[];
  containers: string[];
  extensions: string[];
  tags: string[];
}

export function useMediaVocabulary() {
  return useQuery({
    queryKey: ["media", "vocabulary"] as const,
    queryFn: () => apiClient.get<MediaVocabulary>("/media/vocabulary"),
    staleTime: 60_000,
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

// ── Stage 27: reprobe hook ──────────────────────────────────────
//
// Stage 27 originally exported reprobe + quarantine + unquarantine
// hooks here, plus their bulk variants. Stage 05 (v1.7) removed
// the quarantine hooks alongside the API endpoints they called
// (Section A.0 — "delete means delete"). Reprobe is the only
// surface that survived this stage.
//
// Each mutation invalidates the media list (severity/probe state
// changed) and the per-file detail (drawer reflects the new state).

export interface BulkReprobeResult {
  files_reprobed: number;
  files_failed: number;
  files_not_found: string[];
  files_orphaned: number;
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

// ── v1.9 Stage 2.4 — Operator-initiated delete ─────────────────

export interface DeleteResultRead {
  media_id: string;
  path: string;
  removed_from_disk: boolean;
  trash_path: string | null;
}

export interface BulkDeleteResponse {
  deleted: DeleteResultRead[];
  requested: number;
  not_found: string[];
}

export interface DeleteOneArgs {
  mediaId: string;
  remove_from_disk: boolean;
  reason: string | null;
}

export interface BulkDeleteArgs {
  ids: string[];
  remove_from_disk: boolean;
  reason: string | null;
}

/** Delete one media file. ``remove_from_disk=false`` is index-only
 *  (the file stays on disk and will be re-indexed by the next scan);
 *  ``remove_from_disk=true`` moves the file into the date-bucketed
 *  trash directory under ``data_dir/trash/``. */
export function useDeleteMedia() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mediaId, remove_from_disk, reason }: DeleteOneArgs) =>
      apiClient.delete<DeleteResultRead>(`/media/${mediaId}`, {
        remove_from_disk,
        reason,
      }),
    onSuccess: (_data, args) => {
      invalidateRelated(qc, "media");
      qc.invalidateQueries({ queryKey: ["media", args.mediaId] });
    },
  });
}

export function useBulkDeleteMedia() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ids, remove_from_disk, reason }: BulkDeleteArgs) =>
      apiClient.post<BulkDeleteResponse>("/media/bulk-delete", {
        ids,
        remove_from_disk,
        reason,
      }),
    onSuccess: () => invalidateRelated(qc, "media"),
  });
}
