import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

const buttonVariants = cva(
  cn(
    "inline-flex items-center justify-center gap-1.5 select-none whitespace-nowrap",
    "font-medium border transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1",
    "disabled:opacity-50 disabled:pointer-events-none",
  ),
  {
    variants: {
      variant: {
        default: "bg-surface text-text border-border hover:bg-[var(--hover)]",
        primary: "bg-text text-surface border-transparent hover:bg-[var(--text-2)]",
        accent: "bg-accent text-accent-fg border-transparent hover:bg-accent-2",
        ghost: "bg-transparent text-text-2 border-transparent hover:bg-[var(--hover)]",
        outline: "bg-transparent text-text border-border-strong hover:bg-[var(--hover)]",
        danger:
          "bg-surface text-sev-error border-[color:color-mix(in_oklab,var(--sev-error)_30%,var(--border))] hover:bg-[var(--hover)]",
      },
      size: {
        sm: "h-7 text-[12px] px-2.5 rounded-[5px]",
        md: "h-8 text-[13px] px-3 rounded-md",
        lg: "h-10 text-[14px] px-4 rounded-md",
        icon: "h-7 w-7 p-0 rounded-[5px]",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  ),
);
Button.displayName = "Button";

export { buttonVariants };
