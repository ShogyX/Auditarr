/**
 * Stage 5 — Automation schedules card.
 *
 * Extracted from the inline Schedules section. Same four-way
 * loading / error / empty / data branch as the Optimization-page
 * cards; the pattern recurs because every operational list has the
 * same lifecycle.
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type {
  useDeleteSchedule,
  useRunSchedule,
  useSchedules,
  useUpdateSchedule,
} from "@/hooks/useAutomation";

import { AutomationScheduleRow } from "./AutomationScheduleRow";

export interface AutomationSchedulesCardProps {
  schedules: ReturnType<typeof useSchedules>;
  update: ReturnType<typeof useUpdateSchedule>;
  remove: ReturnType<typeof useDeleteSchedule>;
  run: ReturnType<typeof useRunSchedule>;
  /**
   * Stage 9 close-out: per-schedule edit callback. The card itself
   * is presentation-only; the parent owns the dialog state and
   * decides which schedule to edit. Passing this in rather than
   * owning the dialog state here keeps the card focused.
   */
  onEdit: (scheduleId: string) => void;
}

export function AutomationSchedulesCard({
  schedules,
  update,
  remove,
  run,
  onEdit,
}: AutomationSchedulesCardProps) {
  return (
    <Card>
      <CardHead
        title="Schedules"
        subtitle={
          schedules.data ? `${schedules.data.length} configured` : undefined
        }
      />
      <CardBodyFlush>
        {schedules.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : schedules.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load schedules"
              description={(schedules.error as Error)?.message}
            />
          </div>
        ) : !schedules.data || schedules.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="clock"
              title="No schedules yet"
              description="Create a schedule to run scans, healthchecks, or rule evaluations on a cadence."
            />
          </div>
        ) : (
          schedules.data.map((s) => (
            <AutomationScheduleRow
              key={s.id}
              schedule={s}
              onToggle={() =>
                update.mutate({ id: s.id, patch: { enabled: !s.enabled } })
              }
              onRun={() => run.mutate(s.id)}
              onEdit={() => onEdit(s.id)}
              onDelete={() => {
                if (confirm(`Delete schedule "${s.name}"?`)) {
                  remove.mutate(s.id);
                }
              }}
            />
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
