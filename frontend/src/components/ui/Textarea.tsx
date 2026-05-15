/**
 * Stage 1 — Textarea primitive.
 *
 * Multi-line variant of ``Input``. Same token contract; no size scale (the
 * ``rows`` HTML attribute is the natural sizing knob).
 */

import { forwardRef, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

const BASE = cn(
  "block w-full bg-surface text-text",
  "border border-border rounded-md",
  "outline-none transition-[border-color,box-shadow] duration-100",
  "placeholder:text-muted-2",
  "py-1.5 px-2.5 text-[13px] leading-[1.5]",
  "focus:border-[var(--text)] focus:shadow-[0_0_0_3px_var(--hover)]",
  "disabled:opacity-50 disabled:cursor-not-allowed",
  "aria-[invalid=true]:border-sev-error",
  "aria-[invalid=true]:focus:border-sev-error",
  "aria-[invalid=true]:focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--sev-error)_18%,transparent)]",
);

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** ``mono`` → IBM Plex Mono, 12.5px (useful for JSON, regex). */
  variant?: "default" | "mono";
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, variant = "default", rows = 4, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(BASE, variant === "mono" && "font-mono text-[12.5px]", className)}
      {...rest}
    />
  );
});
