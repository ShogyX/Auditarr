/**
 * Stage 6 — Notifications shared helpers.
 *
 * ``statusClass`` and ``formatDuration`` are Notification-specific
 * (different status vocab from Optimization/Automation).
 * ``initialConfig`` mirrors the Integrations helper but takes a
 * ``NotificationKind``. ``SEVERITY_RANK_OPTIONS`` is the shared menu
 * options for the channel's severity threshold.
 */

import type { NotificationKind } from "@/hooks/useNotifications";

export const SEVERITY_RANK_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 0, label: "Everything (info+)" },
  { value: 20, label: "Info or higher" },
  { value: 40, label: "Warn or higher (default)" },
  { value: 60, label: "High or higher" },
  { value: 80, label: "Error or higher" },
  { value: 100, label: "Critical only" },
];

export function initialConfig(
  kind: NotificationKind,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, meta] of Object.entries(kind.config_schema.properties ?? {})) {
    if (meta.default !== undefined) out[key] = meta.default;
    else if (meta.type === "boolean") out[key] = false;
    else if (meta.type === "integer") out[key] = 0;
    // Stage 15 (audit follow-up): object-typed fields (e.g. the
    // webhook provider's ``headers`` map) default to an empty
    // object so the dialog's KV editor has a stable target to
    // mutate rather than rendering as a string.
    else if (meta.type === "object") out[key] = {};
    else out[key] = "";
  }
  return out;
}

/** Status → pill className. Specific to Notifications:
 *  ``sent`` / ``pending`` / ``skipped`` / ``failed`` / ``error``. */
export function statusClass(status: string): string {
  switch (status) {
    case "sent":
    case "ok":
      return "text-sev-ok border-sev-ok/40 bg-sev-ok/10";
    case "pending":
    case "skipped":
      return "text-sev-info border-sev-info/40 bg-sev-info/10";
    case "failed":
    case "error":
      return "text-sev-error border-sev-error/40 bg-sev-error/10";
    default:
      return "";
  }
}

/** Compact duration formatter for the delivery row. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}
