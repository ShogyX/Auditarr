/** Number / size / time formatters, ported 1:1 from the original UI. */

export const fmtNum = (n: number | null | undefined): string => (n ?? 0).toLocaleString();

export const fmtBytes = (n: number | null | undefined): string => {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
};

export const fmtTB = (n: number): string => `${n.toFixed(1)} TB`;

export const fmtTime = (ts: number | null | undefined): string => {
  if (!ts) return "—";
  const d = (Date.now() - ts) / 1000;
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
};

export const fmtDur = (s: number | null | undefined): string => {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
};

/** Map a severity key (or arbitrary string) to its CSS class.
 *
 * Stage 02 — every entry in the canonical ``SEVERITY_KEYS`` set
 * (``ok info warn high error crit``) must map to a ``sev-*`` class,
 * so the Files-table severity Pill paints with the same colour as
 * the matching swatch on the scope bar (which uses
 * ``var(--sev-<key>)`` directly). Before this stage the bare
 * ``crit`` key fell through to ``sev ?? sev`` and rendered the bare
 * class string ``crit`` which doesn't exist — so the pill picked
 * up no colour at all.
 */
export const sevToClass: Record<string, string> = {
  ok: "sev-ok",
  info: "sev-info",
  warning: "sev-warn",
  warn: "sev-warn",
  high: "sev-high",
  high_bitrate: "sev-warn",
  possible_transcode: "sev-warn",
  always_transcode: "sev-high",
  missing_subtitles: "sev-warn",
  unplayable: "sev-error",
  error: "sev-error",
  corrupt: "sev-error",
  crit: "sev-crit",
  critical: "sev-crit",
  possible_malicious: "sev-crit",
};
