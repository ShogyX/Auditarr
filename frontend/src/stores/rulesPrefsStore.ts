/**
 * Stage 03 — Rules-table column-width preferences.
 *
 * Mirrors the shape of ``filesPrefsStore`` for the Rules surface.
 * Deliberately kept as a separate store rather than merged with
 * the Files prefs:
 *
 *   - different column sets (state / name / severity / actions /
 *     priority / matches / last_eval) vs the Files columns
 *   - different default widths suited to the rule row contents
 *     (Name needs more, numeric columns are tight)
 *   - operators' Files-table tweaks shouldn't bleed into the
 *     Rules table or vice versa
 *
 * Only column widths are persisted at this stage. Per-column
 * filters are not added — the existing ``RulesToolbar`` already
 * carries a single search field that's sufficient for the rule
 * vocabulary. If a need emerges later, the shape mirrors
 * ``filesPrefsStore`` and can be extended cleanly.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** Every column the Rules table renders, in display order. */
export const RULES_COLUMNS = [
  { key: "state", label: "State" },
  { key: "name", label: "Name" },
  { key: "severity", label: "Severity" },
  { key: "actions", label: "Actions" },
  { key: "priority", label: "Priority", num: true },
  { key: "matches", label: "Matches", num: true },
  { key: "last_eval", label: "Last eval" },
  { key: "row_actions", label: "" }, // row-actions cell; no header label
] as const;

export type RulesColumnKey = (typeof RULES_COLUMNS)[number]["key"];

/** Default column widths (px). v1.9 Stage 9.5.1 (OP-1): retuned
 *  to fill a 1920px-class viewport rather than leaving ~800px of
 *  empty space on the right of the table. The previous values
 *  summed to ~1110px; the new values sum to ~1620px, giving the
 *  Name column (which carries the rule title + description) and
 *  Actions column (which lists each action's badge) enough room
 *  to render without truncation on common rule shapes.
 *
 *  Operators with persisted widths keep them — the store only
 *  consults defaults for keys without a persisted value. */
const DEFAULT_COLUMN_WIDTHS: Record<RulesColumnKey, number> = {
  state: 80,
  name: 560,
  severity: 130,
  actions: 280,
  priority: 100,
  matches: 100,
  last_eval: 140,
  row_actions: 120,
};

export const RULES_COLUMN_MIN_WIDTH = 48;

interface RulesPrefsState {
  columnWidths: Partial<Record<RulesColumnKey, number>>;
  setColumnWidth: (key: RulesColumnKey, width: number) => void;
  resetColumnWidths: () => void;
}

const ALL_KEYS = RULES_COLUMNS.map((c) => c.key as RulesColumnKey);

/** Resolve the effective width for a column (persisted or default). */
export function effectiveRulesColumnWidth(
  key: RulesColumnKey,
  persisted: Partial<Record<RulesColumnKey, number>>,
): number {
  const stored = persisted[key];
  if (typeof stored === "number" && stored >= RULES_COLUMN_MIN_WIDTH) {
    return stored;
  }
  return DEFAULT_COLUMN_WIDTHS[key];
}

export const useRulesPrefs = create<RulesPrefsState>()(
  persist(
    (set) => ({
      columnWidths: {},
      setColumnWidth: (key, width) => {
        if (!ALL_KEYS.includes(key)) return;
        const clamped = Math.max(RULES_COLUMN_MIN_WIDTH, Math.round(width));
        set((prev) => ({
          columnWidths: { ...prev.columnWidths, [key]: clamped },
        }));
      },
      resetColumnWidths: () => set({ columnWidths: {} }),
    }),
    {
      name: "auditarr.rules.prefs",
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (!state.columnWidths || typeof state.columnWidths !== "object") {
          state.columnWidths = {};
        }
      },
    },
  ),
);

export { DEFAULT_COLUMN_WIDTHS };
