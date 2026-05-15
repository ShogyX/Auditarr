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
 *  these defaults. */
export const DEFAULT_PROFILE_SETTINGS = {
  video: { codec: "libx265", crf: 22, preset: "medium" },
  audio: { codec: "copy" },
  subtitles: { handling: "copy" },
  output: { container: "mkv", replace_input: true, keep_backup: true },
};
