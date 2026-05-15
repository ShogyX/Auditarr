/**
 * Stage 4 — Rules feature shared helpers.
 *
 * Single source of truth for the small utilities that used to live
 * at the bottom of ``RulesPage.tsx``. Extracted so the table row,
 * the editor, and any future consumer can import the same vocabulary.
 *
 * Nothing here is operational state — these are pure derivations and
 * one DOM-only download helper.
 */

import type { Rule } from "@/hooks/useRules";

/**
 * Severity rank table used to "pick the strongest" when a rule's
 * actions set multiple severities. Mirrors the evaluator's behavior
 * (strongest severity wins). Aliases (``warning``, ``critical``)
 * appear in user-imported rules so they're mapped to the canonical
 * keys.
 */
export const SEV_RANK: Record<string, number> = {
  ok: 10,
  info: 20,
  warn: 30,
  high: 40,
  error: 50,
  crit: 60,
  warning: 30,
  critical: 60,
};

/**
 * Severity displayed in the rules table is derived from the rule's
 * ``set_severity`` actions. When a rule sets multiple severities,
 * surface the highest-rank one.
 */
export function deriveSeverity(rule: Rule): string | null {
  const severities: string[] = [];
  for (const action of rule.definition.actions ?? []) {
    if (action.type === "set_severity" && action.severity) {
      severities.push(action.severity);
    }
  }
  if (severities.length === 0) return null;
  return severities.reduce((a, b) =>
    (SEV_RANK[a] ?? 0) >= (SEV_RANK[b] ?? 0) ? a : b,
  );
}

/** Distinct action types for the Actions column. */
export function uniqueActionTypes(rule: Rule): string[] {
  const set = new Set<string>();
  for (const action of rule.definition.actions ?? []) {
    set.add(action.type);
  }
  return Array.from(set);
}

/**
 * Trigger a JSON download by creating a Blob + anchor + click. Safe
 * in SSR (returns early when ``document`` is undefined) and revokes
 * the object URL after a tick so Safari has time to honor the
 * download.
 */
export function downloadJson(payload: unknown, filename: string): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Tab vocabulary for the Rules page.
 *
 * Stage 10 audit fix (Issue 15): ``automation`` is now a tab here.
 * The Rules page reads schedules + runs + the optimization queue
 * under this tab via the extracted AutomationTabContent. The
 * standalone /automation route redirects to /rules?tab=automation
 * so existing bookmarks keep working.
 */
export type RulesTab = "custom" | "builtin" | "suggestions" | "automation";
