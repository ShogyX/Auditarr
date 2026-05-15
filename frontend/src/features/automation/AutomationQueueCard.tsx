/**
 * Stage 5 — Automation optimization-queue card.
 *
 * A read-only mini-view of the same queue Optimization renders in
 * detail. Automation surfaces it here as part of the operations
 * overview ("schedules, runs, and what's in the queue"); the
 * actionable controls (Run now / Cancel / Retry per item) live on
 * Optimization's full surface.
 *
 * Resists the temptation to use ``OptimizationQueueRow`` here —
 * that row renders action buttons that don't belong on a passive
 * overview card. Keeping a flat read-only row is correct.
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { useOptimizationQueue } from "@/hooks/useAutomation";

import { statusClass } from "./automationShared";

export interface AutomationQueueCardProps {
  queue: ReturnType<typeof useOptimizationQueue>;
}

export function AutomationQueueCard({ queue }: AutomationQueueCardProps) {
  return (
    <Card>
      <CardHead
        title="Optimization queue"
        subtitle={
          queue.data
            ? `${queue.data.filter((q) => q.status === "queued").length} queued`
            : undefined
        }
      />
      <CardBodyFlush>
        {queue.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : queue.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load optimization queue"
              description={(queue.error as Error)?.message}
            />
          </div>
        ) : !queue.data || queue.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="optimize"
              title="Queue is empty"
              description="Rules with a queue_optimization action will populate this list."
            />
          </div>
        ) : (
          queue.data.map((item) => (
            <div
              key={item.id}
              className="px-4 py-2 border-b border-border last:border-b-0 flex items-center gap-3"
            >
              <Icon name="optimize" size={14} className="text-muted-2" />
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] font-mono truncate">
                  {item.media_file_id}
                </div>
                <div className="text-[11px] text-muted-2 mt-0.5">
                  Profile: {item.profile} · Queued{" "}
                  {new Date(item.queued_at).toLocaleString()}
                </div>
              </div>
              <Pill className={statusClass(item.status)}>{item.status}</Pill>
            </div>
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
