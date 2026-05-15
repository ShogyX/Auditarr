/**
 * Stage 1 — Switch primitive.
 *
 * Implements the design package ``.switch`` contract:
 *   - 32 × 18 pill, off track = --border-strong
 *   - on track = --text, thumb slides 14px right
 *   - 14px thumb, --surface, subtle shadow
 *
 * Built on a native ``<button role="switch">`` rather than ``<input
 * type="checkbox">``: better semantics for an action toggle and easier
 * keyboard handling. ``Space`` and ``Enter`` toggle by default because the
 * underlying element is a button.
 *
 * Pattern (controlled):
 *
 *   const [on, setOn] = useState(false);
 *   <Switch checked={on} onCheckedChange={setOn} label="Enabled" />
 */

import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked?: boolean;
  onCheckedChange?: (next: boolean) => void;
  /** Optional accessible label rendered next to the switch. */
  label?: string;
}

export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked = false, onCheckedChange, label, disabled, className, ...rest },
  ref,
) {
  const handleClick = () => {
    if (disabled) return;
    onCheckedChange?.(!checked);
  };

  const track = cn(
    "relative inline-flex items-center w-8 h-[18px] rounded-full transition-colors",
    "outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1",
    "disabled:opacity-50 disabled:cursor-not-allowed",
    checked ? "bg-text" : "bg-border-strong",
  );
  const thumb = cn(
    "absolute top-[2px] left-[2px] w-[14px] h-[14px] rounded-full bg-surface shadow-sm",
    "transition-transform duration-150",
    checked && "translate-x-[14px]",
  );

  const button = (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={handleClick}
      className={cn(track, className)}
      {...rest}
    >
      <span aria-hidden className={thumb} />
    </button>
  );

  if (!label) return button;
  return (
    <label
      className={cn(
        "inline-flex items-center gap-2 text-[13px] text-text",
        disabled && "opacity-50",
      )}
    >
      {button}
      <span>{label}</span>
    </label>
  );
});
