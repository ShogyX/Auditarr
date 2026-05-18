/**
 * Stage 4 — Rules page tab bar (Custom / Built-in / Suggestions).
 *
 * Extracted from the inline ``.segmented`` block in ``RulesPage.tsx``.
 * Preserves the exact DOM contract used by
 * ``RulesPage.stage29.test.tsx`` — segmented buttons with role="tab",
 * aria-selected, and the mono count span. The Stage 1 ``Tabs``
 * primitive is deliberately not adopted here because it renders a
 * different DOM (Radix tab triggers) and would invalidate the
 * count-badge selectors used in three test cases. That migration is
 * a future Stage 4b after a visual baseline is captured.
 */

import type { RulesTab } from "./rulesShared";

export interface RulesTabBarProps {
  tab: RulesTab;
  onTab: (next: RulesTab) => void;
  customCount: number;
  builtinCount: number;
  templatesCount: number;
  suggestionsCount: number;
}

export function RulesTabBar({
  tab,
  onTab,
  customCount,
  builtinCount,
  templatesCount,
  suggestionsCount,
}: RulesTabBarProps) {
  return (
    <div className="segmented" role="tablist" aria-label="Rules view">
      <button
        type="button"
        role="tab"
        aria-selected={tab === "custom"}
        className={tab === "custom" ? "on" : ""}
        onClick={() => onTab("custom")}
      >
        Custom{" "}
        <span className="font-mono text-muted-2 ml-1">{customCount}</span>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={tab === "builtin"}
        className={tab === "builtin" ? "on" : ""}
        onClick={() => onTab("builtin")}
      >
        Built-in{" "}
        <span className="font-mono text-muted-2 ml-1">{builtinCount}</span>
      </button>
      {/* v1.9 Stage 4.4 — Templates tab. The count badge surfaces
          the number of shipped templates the operator can pick
          from. */}
      <button
        type="button"
        role="tab"
        aria-selected={tab === "templates"}
        className={tab === "templates" ? "on" : ""}
        onClick={() => onTab("templates")}
      >
        Templates{" "}
        <span className="font-mono text-muted-2 ml-1">{templatesCount}</span>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={tab === "suggestions"}
        className={tab === "suggestions" ? "on" : ""}
        onClick={() => onTab("suggestions")}
      >
        Suggestions{" "}
        <span className="font-mono text-muted-2 ml-1">{suggestionsCount}</span>
      </button>
      {/* Stage 10 audit fix (Issue 15): Automation tab. No count
          badge — the rules counts come from useRulesPageState, but
          schedule count would mean hoisting useSchedules into that
          hook just for one number. Skipping the badge keeps state
          local to AutomationTabContent. */}
      <button
        type="button"
        role="tab"
        aria-selected={tab === "automation"}
        className={tab === "automation" ? "on" : ""}
        onClick={() => onTab("automation")}
      >
        Automation
      </button>
    </div>
  );
}
