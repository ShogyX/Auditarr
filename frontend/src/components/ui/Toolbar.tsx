/**
 * Stage 1 — Toolbar primitive.
 *
 * A horizontal control rail used above DataGrids, in page headers, and inside
 * Drawer/Modal bodies. Provides three slot groups:
 *   - ``leading``  — primary content (search, filters)
 *   - ``children`` — middle flex region (rarely used; usually a status line)
 *   - ``trailing`` — action buttons aligned right
 *
 * Height is fixed to --toolbar-h so the visual rhythm is consistent across
 * pages. Use ``size="compact"`` only when nested inside a Card head, since
 * the design package's table toolbars are slightly denser than page-level
 * ones.
 *
 * Use with ``FilterBar`` for filter chip arrangements, or by itself for
 * simple search + actions rows.
 */

import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface ToolbarProps extends HTMLAttributes<HTMLDivElement> {
  leading?: ReactNode;
  trailing?: ReactNode;
  size?: "default" | "compact";
}

export function Toolbar({
  leading,
  trailing,
  size = "default",
  className,
  children,
  ...rest
}: ToolbarProps) {
  return (
    <div
      role="toolbar"
      className={cn(
        "flex items-center gap-2 w-full",
        size === "default" ? "h-toolbar-h px-page-x" : "h-9 px-3",
        "bg-surface",
        className,
      )}
      {...rest}
    >
      {leading ? <div className="flex items-center gap-2 min-w-0">{leading}</div> : null}
      {children ? <div className="flex items-center gap-2 min-w-0 flex-1">{children}</div> : <div className="flex-1" />}
      {trailing ? <div className="flex items-center gap-2 shrink-0">{trailing}</div> : null}
    </div>
  );
}

/**
 * FilterBar — sibling primitive to Toolbar. Renders a row of filter chips
 * with optional "Clear all" affordance. Use inside ``Toolbar``'s ``leading``
 * slot or as a stand-alone row below the toolbar for dense filter
 * arrangements.
 *
 *   <FilterBar
 *     filters={[
 *       { label: 'Severity', value: 'High' },
 *       { label: 'Library', value: 'Movies' },
 *     ]}
 *     onClearAll={() => …}
 *   />
 */
export interface FilterChip {
  label: string;
  value: string;
  onRemove?: () => void;
}

export interface FilterBarProps {
  filters: readonly FilterChip[];
  onClearAll?: () => void;
  className?: string;
}

export function FilterBar({ filters, onClearAll, className }: FilterBarProps) {
  if (filters.length === 0) return null;
  return (
    <div className={cn("flex items-center gap-2 flex-wrap", className)}>
      {filters.map((f) => (
        <span
          key={`${f.label}:${f.value}`}
          className={cn(
            "inline-flex items-center gap-1.5 h-6 pl-2 pr-1 rounded-full",
            "bg-surface-sunk border border-border text-[11.5px] text-text-2",
          )}
        >
          <span className="text-muted">{f.label}:</span>
          <span className="font-medium text-text">{f.value}</span>
          {f.onRemove ? (
            <button
              type="button"
              onClick={f.onRemove}
              aria-label={`Remove ${f.label} filter`}
              className="ml-0.5 h-4 w-4 rounded-full inline-flex items-center justify-center hover:bg-[var(--hover)]"
            >
              ×
            </button>
          ) : null}
        </span>
      ))}
      {onClearAll && filters.some((f) => f.onRemove) ? (
        <button
          type="button"
          onClick={onClearAll}
          className="text-[11.5px] text-muted hover:text-text underline-offset-2 hover:underline"
        >
          Clear all
        </button>
      ) : null}
    </div>
  );
}
