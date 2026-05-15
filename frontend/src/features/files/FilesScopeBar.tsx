/**
 * Stage 3 — Files scope bar.
 *
 * Extracted verbatim from Stage 14.1's inline implementation in
 * ``FilesPage.tsx``. The vocabulary, layout, and CSS contract are
 * preserved exactly so the existing FilesPage tests continue to pass.
 *
 * Renders two horizontally-stacked controls:
 *   - segmented "All / Media / Non-media" scope picker
 *   - severity chip row gated by the selected scope
 *
 * Both controls write through the parent (FilesPage) so the URL deep-
 * link logic in ``useFilesPageState`` stays the single source of state.
 */

import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

import {
  SEVERITY_KEYS,
  SEVERITY_META,
  type SeverityKey,
  type ScopeMode,
} from "./filesShared";

export interface FilesScopeBarProps {
  scope: ScopeMode;
  onScope: (s: ScopeMode) => void;
  activeSevs: Set<string>;
  onToggleSev: (key: SeverityKey) => void;
  onAll: () => void;
  onNone: () => void;
}

export function FilesScopeBar({
  scope,
  onScope,
  activeSevs,
  onToggleSev,
  onAll,
  onNone,
}: FilesScopeBarProps) {
  const visibleSevs = SEVERITY_KEYS.filter(
    (k) =>
      scope === "all" ||
      SEVERITY_META[k].scope === "all" ||
      SEVERITY_META[k].scope === scope,
  );
  const allOnInScope = visibleSevs.every((k) => activeSevs.has(k));
  return (
    <Card>
      <div className="scope-bar">
        <div className="scope-bar-head">
          <div className="segmented" role="tablist" aria-label="Severity scope">
            {(
              [
                ["all", "All severities"],
                ["media", "Media"],
                ["non-media", "Non-media"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={scope === k}
                className={scope === k ? "on" : ""}
                onClick={() => onScope(k)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex-1" />
          <span className="text-[11.5px] text-muted-2 mr-1">
            {activeSevs.size} of {visibleSevs.length} severities active
          </span>
          <button
            type="button"
            className="text-[11.5px] text-text-2 hover:text-text border border-border bg-surface-2 rounded-md px-2 py-1"
            onClick={allOnInScope ? onNone : onAll}
          >
            {allOnInScope ? "Hide all" : "Show all"}
          </button>
        </div>
        <div className="scope-bar-chips">
          {visibleSevs.map((key) => {
            const on = activeSevs.has(key);
            const meta = SEVERITY_META[key];
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                className={cn("scope-chip", on && "on")}
                onClick={() => onToggleSev(key)}
                title={`Toggle ${meta.label}`}
              >
                <span
                  className="dot"
                  style={{ background: `var(--${meta.color})` }}
                />
                {meta.label}
              </button>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
