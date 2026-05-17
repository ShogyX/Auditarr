/**
 * Stage 13 (plan §607) — per-card overflow menu.
 *
 * Replaces real drag-and-drop (out of scope per plan §618)
 * with a menu-driven "Replace with…" + "Disable this card"
 * affordance. Click the triple-dot icon in a card head →
 * popover lists disabled cards (replace) plus a "Disable
 * this card" action.
 *
 * The popover closes when:
 *   - the operator picks an option,
 *   - the operator clicks outside,
 *   - the operator presses Escape.
 */

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import {
  DASHBOARD_CARD_KEYS,
  type DashboardCardKey,
  useUiStore,
} from "@/stores/uiStore";

/** Display labels for the canonical card keys. Used by the
 *  menu's "Replace with…" sub-list. */
const CARD_LABELS: Record<string, string> = {
  severity: "Severity overview",
  libraries: "Libraries",
  integrations: "Integrations",
  categories: "Categories",
  live_now: "Live now",
  "top-rules": "Top rules",
  suggestions: "Suggestions",
  "recent-scans": "Recent scans",
  "recent-jobs": "Recent automation runs",
};

export interface DashboardCardMenuProps {
  /** Which card this menu belongs to. */
  cardKey: DashboardCardKey | string;
}

export function DashboardCardMenu({ cardKey }: DashboardCardMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const disabled = useUiStore((s) => s.dashboardDisabled);
  const disableCard = useUiStore((s) => s.disableDashboardCard);
  const replaceCard = useUiStore((s) => s.replaceDashboardCard);

  // Close on outside click + Escape.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center justify-center h-6 w-6 rounded text-muted hover:text-text hover:bg-surface-sunk"
        aria-label="Card options"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={`dashboard-card-menu-${cardKey}`}
      >
        <Icon name="more" size={14} />
      </button>

      {open ? (
        <div
          role="menu"
          className={cn(
            "absolute right-0 top-7 z-20 min-w-[200px]",
            "rounded-md border border-border bg-surface shadow-lg",
            "py-1 text-[12px]",
          )}
          data-testid={`dashboard-card-menu-popover-${cardKey}`}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              disableCard(cardKey);
              setOpen(false);
            }}
            className="block w-full px-3 py-1.5 text-left hover:bg-surface-sunk"
            data-testid={`dashboard-card-disable-${cardKey}`}
          >
            Disable this card
          </button>

          {disabled.length > 0 ? (
            <>
              <div className="border-t border-border my-1" />
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-muted-2">
                Replace with…
              </div>
              {disabled.map((other) => (
                <button
                  type="button"
                  key={other}
                  role="menuitem"
                  onClick={() => {
                    replaceCard(cardKey, other);
                    setOpen(false);
                  }}
                  className="block w-full px-3 py-1.5 text-left hover:bg-surface-sunk"
                  data-testid={`dashboard-card-replace-${cardKey}-with-${other}`}
                >
                  {CARD_LABELS[other] ?? other}
                </button>
              ))}
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Stage 13 (plan §607) — "Disabled cards" rail rendered at
 * the bottom of the dashboard. Each entry has a "Restore"
 * button that moves the card back into the active grid.
 */
export function DashboardDisabledRail() {
  const disabled = useUiStore((s) => s.dashboardDisabled);
  const enableCard = useUiStore((s) => s.enableDashboardCard);
  const [collapsed, setCollapsed] = useState(disabled.length === 0);

  if (disabled.length === 0) return null;

  return (
    <div
      className="mt-6 rounded-md border border-border bg-surface px-4 py-3"
      data-testid="dashboard-disabled-rail"
    >
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex w-full items-center justify-between text-[12px] font-medium text-text"
      >
        <span>
          Disabled cards
          <span className="ml-2 text-muted">({disabled.length})</span>
        </span>
        <Icon
          name={collapsed ? "chev_down" : "chev_up"}
          size={14}
          className="text-muted"
        />
      </button>

      {!collapsed ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {disabled.map((key) => (
            <div
              key={key}
              className="inline-flex items-center gap-2 rounded border border-border bg-surface-sunk px-2 py-1 text-[12px]"
              data-testid={`dashboard-disabled-card-${key}`}
            >
              <span>{CARD_LABELS[key] ?? key}</span>
              <button
                type="button"
                onClick={() => enableCard(key)}
                className="text-accent hover:underline text-[11px]"
                data-testid={`dashboard-restore-card-${key}`}
              >
                Restore
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Re-export the canonical key list for consumers. */
export { DASHBOARD_CARD_KEYS };
