import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "bg-surface border border-border rounded-[var(--radius)] shadow-sm overflow-hidden",
        className,
      )}
      {...props}
    />
  );
}

interface CardHeadProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}

export function CardHead({
  title,
  subtitle,
  actions,
  className,
  children,
  ...props
}: CardHeadProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 px-4 h-11 border-b border-border",
        className,
      )}
      {...props}
    >
      {title ? (
        <div className="flex items-baseline gap-2 min-w-0">
          <h3 className="text-[13px] font-semibold tracking-tight m-0 truncate">{title}</h3>
          {subtitle ? <span className="text-[11.5px] text-muted truncate">{subtitle}</span> : null}
        </div>
      ) : (
        children
      )}
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

export function CardBodyFlush({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-0", className)} {...props} />;
}
