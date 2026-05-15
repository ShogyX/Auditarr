/**
 * Stage 5 — Optimization queue card.
 *
 * Extracted from the inline Queue section of ``OptimizationPage``.
 * Renders the card chrome + the four-way loading / error / empty /
 * data branch. Row rendering is delegated to
 * ``OptimizationQueueRow`` (which is also re-usable by future
 * operations dashboards — the row is the canonical "one queued item"
 * view).
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { useOptimizationQueueDetail } from "@/hooks/useOptimization";

import { OptimizationQueueRow } from "./OptimizationQueueRow";

export interface OptimizationQueueCardProps {
  queue: ReturnType<typeof useOptimizationQueueDetail>;
  /** Page-size label rendered in the card subtitle (e.g. "of last 50"). */
  pageSize?: number;
}

export function OptimizationQueueCard({
  queue,
  pageSize = 50,
}: OptimizationQueueCardProps) {
  return (
    <Card>
      <CardHead
        title="Queue"
        subtitle={
          queue.data ? `${queue.data.length} of last ${pageSize}` : undefined
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
              description="Rules with a queue_optimization action populate this list."
            />
          </div>
        ) : (
          queue.data.map((item) => (
            <OptimizationQueueRow key={item.id} item={item} />
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
