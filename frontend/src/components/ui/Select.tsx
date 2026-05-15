/**
 * Stage 1 — Select primitive.
 *
 * Native ``<select>`` with the same token contract as ``Input``. We use a
 * native element rather than Radix Select on purpose:
 *   - matches the design package ``.select`` contract (single-line, in-place)
 *   - avoids the size and complexity of a custom popover for what is usually
 *     a flat enum
 *   - native a11y for free (keyboard, screen reader)
 *
 * Use ``Popover`` + ``DropdownMenu`` from Radix when you need rich items
 * (icons, badges, search). Use ``Select`` here when you just need a value.
 */

import { forwardRef, type SelectHTMLAttributes } from "react";

import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

const BASE = cn(
  "block w-full bg-surface text-text",
  "border border-border rounded-md",
  "outline-none transition-[border-color,box-shadow] duration-100",
  "appearance-none pr-7",
  "focus:border-[var(--text)] focus:shadow-[0_0_0_3px_var(--hover)]",
  "disabled:opacity-50 disabled:cursor-not-allowed",
  "aria-[invalid=true]:border-sev-error",
);

const SIZES = {
  sm: "h-7 text-[12px] pl-2",
  md: "h-8 text-[13px] pl-2.5",
} as const;

export interface SelectProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  size?: keyof typeof SIZES;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, size = "md", children, ...rest },
  ref,
) {
  return (
    <div className="relative inline-block w-full">
      <select ref={ref} className={cn(BASE, SIZES[size], className)} {...rest}>
        {children}
      </select>
      <Icon
        name="chev_down"
        size={14}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted"
      />
    </div>
  );
});
