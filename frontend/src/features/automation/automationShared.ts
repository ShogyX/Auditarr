/**
 * Stage 5 — Automation feature shared helpers.
 *
 * Single source of truth for the helpers that used to live at the
 * bottom of ``AutomationPage.tsx``.
 *
 * ``statusClass`` mirrors the schedule + job-run + queue-item status
 * vocabulary. It is a superset of Optimization's status vocabulary
 * because Automation also surfaces ``degraded`` / ``ok`` / ``error``
 * (from integration health checks that schedules can run). The
 * Addendum item #6 ("Status enums normalisation") will unify these
 * eventually; for now they live alongside each other and that's fine.
 *
 * ``formatCron`` reads a partial cron-spec dict (``{hour: 3,
 * minute: 0}``) into a human-readable string. ``formatDuration``
 * formats a ms duration as ``842ms`` / ``12.3s`` / ``3.4m``.
 */

/** Schedule / run / queue status → pill className. */
export function statusClass(status: string): string {
  switch (status) {
    case "completed":
    case "ok":
      return "text-sev-ok border-sev-ok/40 bg-sev-ok/10";
    case "running":
    case "queued":
      return "text-sev-info border-sev-info/40 bg-sev-info/10";
    case "failed":
    case "error":
      return "text-sev-error border-sev-error/40 bg-sev-error/10";
    case "degraded":
      return "text-sev-warn border-sev-warn/40 bg-sev-warn/10";
    default:
      return "";
  }
}

/** Render a partial cron-spec dict for the schedule row. */
export function formatCron(cron: Record<string, unknown>): string {
  const parts: string[] = [];
  if ("hour" in cron) parts.push(`hour ${cron.hour}`);
  if ("minute" in cron) parts.push(`minute ${cron.minute}`);
  if ("day" in cron) parts.push(`day ${cron.day}`);
  if ("weekday" in cron) parts.push(`weekday ${cron.weekday}`);
  if ("month" in cron) parts.push(`month ${cron.month}`);
  return parts.length === 0 ? "every minute" : parts.join(", ");
}

/** Render a duration in ms as a compact human-readable string. */
export function formatDuration(ms: number | null): string {
  if (ms === null) return "running";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}
