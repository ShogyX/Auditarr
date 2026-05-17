/**
 * Stage 5 — Optimization feature shared helpers.
 *
 * Single source of truth for the helpers that used to live at the
 * bottom of ``OptimizationPage.tsx``. Now imported by the page,
 * sub-cards, and the queue-row component shared with Automation.
 *
 * ``statusClass`` is the optimization-status → pill-className mapping
 * (queued/running/completed/failed/cancelled/skipped). It is *not*
 * identical to Automation's status mapping (which also handles
 * ``degraded``/``ok``/``error`` aliases for schedule + integration
 * health); the two stay separate until the Addendum item #6 ("Status
 * enums normalisation") pass.
 *
 * ``fmtBytes`` formats a byte count for the "saved 1.2 GB (28%)"
 * disclosure in queue rows. The page-wide ``fmtNum`` helper from
 * ``lib/format`` covers most other cases.
 *
 * ``DEFAULT_PROFILE_SETTINGS`` is the seed JSON the create dialog
 * uses for a fresh profile.
 */

/** Optimization status classes used by the queue-row pill. */
export function statusClass(status: string): string {
  switch (status) {
    case "completed":
      return "text-sev-ok border-sev-ok/40 bg-sev-ok/10";
    case "running":
    case "queued":
      return "text-sev-info border-sev-info/40 bg-sev-info/10";
    case "failed":
      return "text-sev-error border-sev-error/40 bg-sev-error/10";
    case "cancelled":
    case "skipped":
      return "text-muted-2 border-border bg-surface-2";
    default:
      return "";
  }
}

/** Progress-bar fill class — matches the status pill's colour family. */
export function progressClass(status: string): string {
  if (status === "completed") return "bg-sev-ok";
  if (status === "failed") return "bg-sev-error";
  if (status === "cancelled" || status === "skipped") return "bg-muted-2";
  return "bg-sev-info";
}

/** Compact byte formatter (``1.2 GB`` / ``842 KB`` / ``96 B``). */
export function fmtBytes(bytes: number): string {
  const abs = Math.abs(bytes);
  if (abs < 1024) return `${bytes} B`;
  if (abs < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (abs < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

/** Seed JSON for a fresh optimization profile. Operators usually
 *  customize only the video block + container; the rest stays at
 *  these defaults.
 *
 *  Stage 07 (v1.7) — defaults include the four cross-cutting
 *  fields: ``transcode_scope`` (both streams), ``tag_scope``
 *  (empty list = no requirement), ``routing_target`` (in-process
 *  ffmpeg runner), ``schedule_window`` (None = always allowed).
 */
export const DEFAULT_PROFILE_SETTINGS = {
  video: { codec: "libx265", crf: 22, preset: "medium" },
  audio: { codec: "copy" },
  subtitles: { handling: "copy" },
  output: { container: "mkv", replace_input: true, keep_backup: true },
  transcode_scope: "video_and_audio",
  tag_scope: [],
  routing_target: "in_process",
};

// ── Stage 07 (v1.7) — routing-target option matrix ────────────
// Per plan §409: the form only exposes options known to work for
// the chosen routing target. The map's shape is intentionally
// flat — one boolean per knob — so the dialog can do a simple
// ``OPTIONS_BY_TARGET[routing_target][knob]`` check.
//
// in_process — local ffmpeg runner. Every knob applies.
// plex      — Plex's transcoder accepts codec family + container
//             + a quality level. Preset / CRF / max_bitrate /
//             scale_height are NOT meaningful (Plex translates the
//             "quality" level to its own internal staircase).
// jellyfin  — same shape as plex; Jellyfin's transcoder API is
//             close enough that the same set applies.
// tdarr     — Tdarr accepts arbitrary ffmpeg argv via its plugin
//             contract. We expose codec + container + scale; the
//             rest gets driven by the Tdarr plugin's own
//             configuration (CRF / preset live in Tdarr).
//
// Stage 08 wires the actual provider sides; Stage 07 just makes
// the UI surface the right options to the right operator.
export type RoutingTarget = "in_process" | "plex" | "jellyfin" | "tdarr";

export interface RoutingTargetOptions {
  video_codec: boolean;
  audio_codec: boolean;
  container: boolean;
  crf: boolean;
  preset: boolean;
  max_bitrate_kbps: boolean;
  scale_height: boolean;
  subtitles: boolean;
  /** Free-form ffmpeg extra args only meaningful for in-process. */
  extra_args: boolean;
}

export const OPTIONS_BY_TARGET: Record<RoutingTarget, RoutingTargetOptions> = {
  in_process: {
    video_codec: true,
    audio_codec: true,
    container: true,
    crf: true,
    preset: true,
    max_bitrate_kbps: true,
    scale_height: true,
    subtitles: true,
    extra_args: true,
  },
  plex: {
    video_codec: true,
    audio_codec: true,
    container: true,
    crf: false,
    preset: false,
    max_bitrate_kbps: false,
    scale_height: false,
    subtitles: true,
    extra_args: false,
  },
  jellyfin: {
    video_codec: true,
    audio_codec: true,
    container: true,
    crf: false,
    preset: false,
    max_bitrate_kbps: false,
    scale_height: false,
    subtitles: true,
    extra_args: false,
  },
  tdarr: {
    video_codec: true,
    audio_codec: true,
    container: true,
    crf: false,
    preset: false,
    max_bitrate_kbps: false,
    scale_height: true,
    subtitles: true,
    extra_args: false,
  },
};

/** Stage 07 (v1.7) — the four routing-target options the dialog
 *  exposes in its dropdown, with display labels. */
export const ROUTING_TARGET_LABELS: { value: RoutingTarget; label: string }[] = [
  { value: "in_process", label: "In-process ffmpeg (this host)" },
  { value: "plex", label: "Plex transcoder" },
  { value: "jellyfin", label: "Jellyfin transcoder" },
  { value: "tdarr", label: "Tdarr" },
];

/** Stage 07 (v1.7) — the three transcode-scope options + labels. */
export const TRANSCODE_SCOPE_LABELS: {
  value: "video_and_audio" | "video_only" | "audio_only";
  label: string;
}[] = [
  { value: "video_and_audio", label: "Video and audio" },
  { value: "video_only", label: "Video only (passthrough audio)" },
  { value: "audio_only", label: "Audio only (passthrough video)" },
];

/** Resolve the browser's IANA timezone. Used by the schedule-
 *  window timezone control to default the field to whatever the
 *  operator's clock says — typically the same as the server's,
 *  but the dialog renders a small warning when they differ. */
export function getBrowserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
