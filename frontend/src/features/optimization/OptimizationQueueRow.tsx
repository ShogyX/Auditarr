/**
 * Stage 5 — Optimization queue row.
 *
 * Extracted from the inline ``QueueRow`` in ``OptimizationPage.tsx``.
 * Lives in ``features/optimization/`` because the queue is an
 * optimization concept; ``AutomationPage`` re-uses the simpler card
 * rendering (without per-row actions) since its queue card is a
 * read-only overview, not a control surface.
 *
 * Status-driven affordance set:
 *   - queued                → Run now (synchronous) · Cancel
 *   - running               → Cancel
 *   - failed / cancelled /  → Retry
 *     skipped
 *   - completed             → no actions; the row's progress bar
 *                              is filled with the OK colour
 *
 * Progress bar shows whenever the item is running or has a
 * non-zero progress percentage in a terminal state — the latter
 * keeps the "completed" / "failed" rows informative about how far
 * the worker got before stopping.
 */

import { useMemo } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import {
  useCancelOptimizationItem,
  useRetryOptimizationItem,
  useRunOptimizationItem,
  type OptimizationItem,
} from "@/hooks/useOptimization";
import { cn } from "@/lib/cn";

import { fmtBytes, progressClass, statusClass } from "./optimizationShared";

export interface OptimizationQueueRowProps {
  item: OptimizationItem;
}

export function OptimizationQueueRow({ item }: OptimizationQueueRowProps) {
  const runItem = useRunOptimizationItem();
  const cancel = useCancelOptimizationItem();
  const retry = useRetryOptimizationItem();
  const canRunNow = item.status === "queued";
  const canCancel = item.status === "queued" || item.status === "running";
  const canRetry =
    item.status === "failed" ||
    item.status === "cancelled" ||
    item.status === "skipped";

  // Memoize the savings calc so the row doesn't recompute it on every
  // unrelated parent render (queue updates are frequent). The math is
  // trivial but the row re-renders are not.
  const savings = useMemo(() => {
    if (item.original_size_bytes == null || item.optimized_size_bytes == null) {
      return null;
    }
    const saved = item.original_size_bytes - item.optimized_size_bytes;
    const pct = (saved / item.original_size_bytes) * 100;
    return { saved, pct };
  }, [item.original_size_bytes, item.optimized_size_bytes]);

  return (
    <div className="px-4 py-2.5 border-b border-border last:border-b-0">
      <div className="flex items-center gap-3">
        <Icon name="optimize" size={14} className="text-muted-2 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[12.5px] font-mono truncate">
              {item.media_file_id}
            </span>
            <Tag>{item.profile}</Tag>
            <Pill className={statusClass(item.status)}>{item.status}</Pill>
          </div>
          <div className="text-[11px] text-muted-2 mt-0.5 truncate">
            Queued {new Date(item.queued_at).toLocaleString()}
            {item.started_at
              ? ` · Started ${new Date(item.started_at).toLocaleString()}`
              : ""}
            {savings
              ? ` · Saved ${fmtBytes(savings.saved)} (${savings.pct.toFixed(0)}%)`
              : ""}
            {item.error ? ` · ${item.error}` : ""}
          </div>
        </div>
        {canRunNow ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => runItem.mutate(item.id)}
            title="Run now"
            disabled={runItem.isPending}
          >
            <Icon name="play" size={12} />
          </Button>
        ) : null}
        {canCancel ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => cancel.mutate(item.id)}
            title="Cancel"
            disabled={cancel.isPending}
          >
            <Icon name="x" size={12} />
          </Button>
        ) : null}
        {canRetry ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => retry.mutate(item.id)}
            title="Retry"
            disabled={retry.isPending}
          >
            <Icon name="refresh" size={12} />
          </Button>
        ) : null}
      </div>
      {/* Progress bar — only show for active or recently-active items. */}
      {item.status === "running" ||
      (item.progress_pct > 0 && item.status !== "queued") ? (
        <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-md bg-surface-sunk">
          <div
            className={cn("h-full", progressClass(item.status))}
            style={{ width: `${item.progress_pct}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}
