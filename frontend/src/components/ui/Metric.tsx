/**
 * Stage 1 — Metric primitive.
 *
 * KPI card used on the Dashboard. Honours the design package ``.metric``
 * contract:
 *   - --muted label, uppercase 11px tracked
 *   - large value (24px mono optional via ``mono``)
 *   - optional foot line with ``.metric-delta.up | .down`` indicator
 *
 * The component composes the existing ``Card`` primitive so card behaviour
 * (border / radius / surface) stays single-sourced.
 *
 * Usage:
 *
 *   <Metric
 *     label="Files scanned"
 *     value={48127}
 *     mono
 *     delta={{ direction: 'up', text: '+ 1.2k vs last 7d' }}
 *   />
 */

import type { ReactNode } from "react";

import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

export interface MetricDelta {
  direction: "up" | "down" | "flat";
  text: ReactNode;
}

export interface MetricProps {
  label: ReactNode;
  value: ReactNode;
  /** Render the value in tabular monospace. */
  mono?: boolean;
  /** Auxiliary detail beneath the value. */
  foot?: ReactNode;
  delta?: MetricDelta;
  className?: string;
}

export function Metric({ label, value, mono, foot, delta, className }: MetricProps) {
  return (
    <Card className={cn("p-4 flex flex-col gap-1.5", className)}>
      <div className="text-[11px] uppercase tracking-[0.06em] text-muted font-semibold">
        {label}
      </div>
      <div
        className={cn(
          "text-[24px] font-semibold leading-none text-text",
          mono && "font-mono tracking-tight",
        )}
      >
        {value}
      </div>
      {(delta || foot) && (
        <div className="flex items-center gap-2 mt-1 text-[12px]">
          {delta ? (
            <span
              className={cn(
                "inline-flex items-center gap-1",
                delta.direction === "up" && "text-sev-ok",
                delta.direction === "down" && "text-sev-error",
                delta.direction === "flat" && "text-muted",
              )}
            >
              {delta.direction === "up" ? (
                <Icon name="chev_up" size={12} />
              ) : delta.direction === "down" ? (
                <Icon name="chev_down" size={12} />
              ) : (
                <span aria-hidden>—</span>
              )}
              {delta.text}
            </span>
          ) : null}
          {foot ? <span className="text-muted">{foot}</span> : null}
        </div>
      )}
    </Card>
  );
}
