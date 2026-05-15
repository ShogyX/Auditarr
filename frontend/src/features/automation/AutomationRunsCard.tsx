/**
 * Stage 5 — Automation recent-runs card.
 *
 * Extracted from the inline Recent runs section. Read-only;
 * pure-presentational; mutation hooks are not needed because the
 * runs list is not directly mutable by the operator (they're driven
 * by the scheduler firing or by Schedules' Run-now button).
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { useJobRuns } from "@/hooks/useAutomation";

import { AutomationRunRow } from "./AutomationRunRow";

export interface AutomationRunsCardProps {
  runs: ReturnType<typeof useJobRuns>;
  pageSize?: number;
}

export function AutomationRunsCard({
  runs,
  pageSize = 20,
}: AutomationRunsCardProps) {
  return (
    <Card>
      <CardHead
        title="Recent runs"
        subtitle={
          runs.data ? `${runs.data.length} of last ${pageSize}` : undefined
        }
      />
      <CardBodyFlush>
        {runs.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : runs.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load recent runs"
              description={(runs.error as Error)?.message}
            />
          </div>
        ) : !runs.data || runs.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="clock"
              title="No runs yet"
              description="Job runs appear here once schedules fire or you run them manually."
            />
          </div>
        ) : (
          runs.data.map((r) => <AutomationRunRow key={r.id} run={r} />)
        )}
      </CardBodyFlush>
    </Card>
  );
}
