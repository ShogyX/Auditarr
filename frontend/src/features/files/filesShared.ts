/**
 * Stage 3 — Files feature shared module.
 *
 * Single source of truth for the small constants and type aliases that
 * used to live inline in ``FilesPage.tsx``. Extracted so every sub-
 * component imports the same vocabulary instead of redeclaring it.
 *
 * Nothing here is operational state — these are static contracts that
 * shape the filter UI and severity scope.
 */

export const CATEGORY_OPTIONS = [
  { value: "", label: "All categories" },
  { value: "media", label: "Media" },
  { value: "subtitle", label: "Subtitles" },
  { value: "image", label: "Images" },
  { value: "metadata", label: "Metadata" },
  { value: "junk", label: "Junk" },
  { value: "unknown", label: "Unknown" },
] as const;

export const SEVERITY_KEYS = [
  "ok",
  "info",
  "warn",
  "high",
  "error",
  "crit",
] as const;

export type SeverityKey = (typeof SEVERITY_KEYS)[number];
export type ScopeMode = "all" | "media" | "non-media";
// ``QuarantineView`` lived here pre-Stage-05; the quarantine
// workflow is gone (Section A.0 of the v1.7 addendum).

export const SEVERITY_META: Record<
  SeverityKey,
  { label: string; scope: "all" | "media" | "non-media"; color: string }
> = {
  ok: { label: "OK", scope: "all", color: "sev-ok" },
  info: { label: "Info", scope: "non-media", color: "sev-info" },
  warn: { label: "Warning", scope: "media", color: "sev-warn" },
  high: { label: "High", scope: "media", color: "sev-high" },
  error: { label: "Error", scope: "media", color: "sev-error" },
  crit: { label: "Critical", scope: "all", color: "sev-crit" },
};
