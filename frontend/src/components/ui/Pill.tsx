import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";
import { sevToClass } from "@/lib/format";

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  /** Severity key — controls color and dot. */
  sev?: string;
  /** Render as a solid (filled) pill instead of outline. */
  solid?: boolean;
  children: ReactNode;
}

export function Pill({ sev, solid, className, children, ...props }: PillProps) {
  const sevCls = sev ? (sevToClass[sev] ?? sev) : undefined;
  return (
    <span className={cn("pill", sevCls, solid && "solid", className)} {...props}>
      {sev && !solid ? <span className={cn("dot", sevCls)} /> : null}
      {children}
    </span>
  );
}

interface TagProps extends HTMLAttributes<HTMLSpanElement> {
  accent?: boolean;
  children: ReactNode;
}

export function Tag({ accent, className, children, ...props }: TagProps) {
  return (
    <span className={cn("tag", accent && "accent", className)} {...props}>
      {children}
    </span>
  );
}
