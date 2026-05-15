/**
 * Stage 1 — Input / TextInput primitive.
 *
 * Token contract (matches design package ``.input``):
 *   - 1px solid --border, radius 6px
 *   - 7px / 10px padding, 13px text
 *   - focus: --text border + 3px --hover ring
 *
 * Variants:
 *   - ``mono``   → IBM Plex Mono, 12.5px
 *   - ``search`` → leading magnifier icon, padding-left for icon clearance
 *   - ``size="sm" | "md"`` (md default)
 *
 * The component is uncontrolled by default. Add ``value`` + ``onChange`` to
 * control. ``aria-invalid`` is forwarded so callers can mark validation
 * failures without a separate prop.
 */

import { forwardRef, type InputHTMLAttributes } from "react";

import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

const BASE = cn(
  "block w-full bg-surface text-text",
  "border border-border rounded-md",
  "outline-none transition-[border-color,box-shadow] duration-100",
  "placeholder:text-muted-2",
  "focus:border-[var(--text)] focus:shadow-[0_0_0_3px_var(--hover)]",
  "disabled:opacity-50 disabled:cursor-not-allowed",
  // aria-invalid colouring
  "aria-[invalid=true]:border-sev-error",
  "aria-[invalid=true]:focus:border-sev-error",
  "aria-[invalid=true]:focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--sev-error)_18%,transparent)]",
);

const SIZES = {
  sm: "h-7 text-[12px] px-2",
  md: "h-8 text-[13px] px-2.5",
} as const;

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  /** ``mono`` → tabular monospace; ``search`` → leading magnifier; default plain. */
  variant?: "default" | "mono" | "search";
  size?: keyof typeof SIZES;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, variant = "default", size = "md", type = "text", ...rest },
  ref,
) {
  if (variant === "search") {
    return (
      <div className="relative">
        <Icon
          name="search"
          size={14}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
        />
        <input
          ref={ref}
          type={type}
          className={cn(BASE, SIZES[size], "pl-8", className)}
          {...rest}
        />
      </div>
    );
  }
  return (
    <input
      ref={ref}
      type={type}
      className={cn(
        BASE,
        SIZES[size],
        variant === "mono" && "font-mono text-[12.5px] tracking-tight",
        className,
      )}
      {...rest}
    />
  );
});
