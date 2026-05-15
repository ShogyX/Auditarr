/**
 * Stage 1 — Tabs primitive.
 *
 * Built on ``@radix-ui/react-tabs``. Two surface variants:
 *
 *   - ``variant="line"``   (default) — underline-style tabs that read as part
 *     of a page header or content area. Used by the design package's tabbed
 *     surfaces (Rules → Custom/Built-in/Suggestions, Files → All/Library/…).
 *
 *   - ``variant="segmented"`` — pill-style segmented control for binary or
 *     three-way mode toggles. Backed by the existing ``.segmented`` CSS class
 *     family. Prefer this for "Compact / Comfortable" or "Day / Week / Month"
 *     view toggles; use ``line`` for navigation between operational surfaces.
 *
 * Items support an optional ``count`` badge — used heavily on Rules and
 * Suggestions tabs.
 */

import * as RTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface TabItem {
  value: string;
  label: ReactNode;
  /** Optional numeric badge shown to the right of the label. */
  count?: number;
  disabled?: boolean;
}

export interface TabsProps {
  value: string;
  onValueChange: (next: string) => void;
  items: readonly TabItem[];
  variant?: "line" | "segmented";
  /** Optional ARIA label for the tablist. */
  ariaLabel?: string;
  /** Children are panels. Use ``<TabsPanel value="…">…</TabsPanel>``. */
  children?: ReactNode;
  className?: string;
}

export function Tabs({
  value,
  onValueChange,
  items,
  variant = "line",
  ariaLabel,
  children,
  className,
}: TabsProps) {
  return (
    <RTabs.Root value={value} onValueChange={onValueChange} className={cn("flex flex-col", className)}>
      {variant === "line" ? (
        <RTabs.List
          aria-label={ariaLabel}
          className="flex items-end gap-1 border-b border-border"
        >
          {items.map((item) => (
            <RTabs.Trigger
              key={item.value}
              value={item.value}
              disabled={item.disabled}
              className={cn(
                "inline-flex items-center gap-2 px-3 h-9 text-[13px] text-text-2",
                "border-b-2 border-transparent -mb-px",
                "hover:text-text disabled:opacity-50 disabled:cursor-not-allowed",
                "data-[state=active]:text-text data-[state=active]:border-text",
                "data-[state=active]:font-medium",
                "transition-colors",
              )}
            >
              <span>{item.label}</span>
              {typeof item.count === "number" ? (
                <span
                  className={cn(
                    "min-w-[20px] h-[18px] inline-flex items-center justify-center px-1.5",
                    "rounded-full text-[10.5px] font-mono font-semibold",
                    "bg-surface-sunk text-text-2 border border-border",
                  )}
                >
                  {item.count.toLocaleString()}
                </span>
              ) : null}
            </RTabs.Trigger>
          ))}
        </RTabs.List>
      ) : (
        <RTabs.List
          aria-label={ariaLabel}
          className={cn(
            "inline-flex bg-surface-sunk border border-border rounded-md p-0.5 gap-0.5 self-start",
          )}
        >
          {items.map((item) => (
            <RTabs.Trigger
              key={item.value}
              value={item.value}
              disabled={item.disabled}
              className={cn(
                "inline-flex items-center gap-1.5 px-2.5 h-6 rounded-[4px] text-[12px] font-medium",
                "text-muted hover:text-text",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "data-[state=active]:bg-surface data-[state=active]:text-text",
                "data-[state=active]:shadow-sm",
                "transition-colors",
              )}
            >
              <span>{item.label}</span>
              {typeof item.count === "number" ? (
                <span className="font-mono text-[10.5px] text-muted">{item.count.toLocaleString()}</span>
              ) : null}
            </RTabs.Trigger>
          ))}
        </RTabs.List>
      )}
      {children}
    </RTabs.Root>
  );
}

export function TabsPanel({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <RTabs.Content value={value} className={cn("focus:outline-none", className)}>
      {children}
    </RTabs.Content>
  );
}
