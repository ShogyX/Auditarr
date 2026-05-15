import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

export function LoadingState({
  label = "Loading…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-center gap-2 text-muted text-[12px] py-12",
        className,
      )}
    >
      <span className="inline-block h-3 w-3 rounded-full border-2 border-border-strong border-t-accent animate-spin" />
      {label}
    </div>
  );
}

export function EmptyState({
  icon = "info",
  title,
  description,
  action,
  className,
}: {
  icon?: IconName;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center text-center gap-2 py-14 px-6 text-text-2",
        className,
      )}
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-sunk text-muted">
        <Icon name={icon} size={18} />
      </div>
      <div className="text-[14px] font-semibold">{title}</div>
      {description ? <div className="text-[12px] text-muted max-w-sm">{description}</div> : null}
      {action}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  description,
  action,
  className,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center text-center gap-2 py-14 px-6", className)}>
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-sunk text-sev-error">
        <Icon name="x" size={18} />
      </div>
      <div className="text-[14px] font-semibold">{title}</div>
      {description ? <div className="text-[12px] text-muted max-w-sm">{description}</div> : null}
      {action}
    </div>
  );
}
