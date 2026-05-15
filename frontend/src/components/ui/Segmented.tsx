/**
 * Stage 1 — Segmented control primitive.
 *
 * Pill-style mode toggle. The design package's ``.segmented`` class family is
 * the visual contract; this is the canonical React surface for it.
 *
 * Use ``Segmented`` for mutually-exclusive view-mode toggles (e.g. "Day /
 * Week / Month", "Compact / Comfortable"). For navigation between
 * operational surfaces (Custom / Built-in / Suggestions), use ``Tabs``
 * instead — they communicate hierarchy differently.
 *
 * Usage:
 *
 *   <Segmented
 *     value={range}
 *     onChange={setRange}
 *     options={[
 *       { value: '7d', label: '7d' },
 *       { value: '30d', label: '30d' },
 *       { value: '90d', label: '90d' },
 *     ]}
 *   />
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
  ariaLabel?: string;
  disabled?: boolean;
}

export interface SegmentedProps<T extends string> {
  value: T;
  onChange: (next: T) => void;
  options: readonly SegmentedOption<T>[];
  ariaLabel?: string;
  className?: string;
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  className,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex bg-surface-sunk border border-border rounded-md p-0.5 gap-0.5",
        className,
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={opt.ariaLabel}
            disabled={opt.disabled}
            onClick={() => onChange(opt.value)}
            className={cn(
              "inline-flex items-center justify-center px-2.5 h-6 rounded-[4px]",
              "text-[12px] font-medium transition-colors",
              "disabled:opacity-50 disabled:cursor-not-allowed",
              active
                ? "bg-surface text-text shadow-sm"
                : "bg-transparent text-muted hover:text-text",
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
