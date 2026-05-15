/**
 * Stage 5 — Automation schedule row.
 *
 * Extracted from the inline ``ScheduleRow`` in ``AutomationPage.tsx``.
 * Renders one schedule's row with cron disclosure + status pill + the
 * Run/Toggle/Delete action triple.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import type { Schedule } from "@/hooks/useAutomation";

import { formatCron, statusClass } from "./automationShared";

export interface AutomationScheduleRowProps {
  schedule: Schedule;
  onToggle: () => void;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

export function AutomationScheduleRow({
  schedule,
  onToggle,
  onRun,
  onEdit,
  onDelete,
}: AutomationScheduleRowProps) {
  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0 flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium truncate">
            {schedule.name}
          </span>
          <Tag>{schedule.job_kind}</Tag>
          {/* Stage 8 audit fix (Issue 10): always-present state
              pill instead of a conditional "disabled" pill. The
              operator now sees the state at a glance on every row,
              not just when paused. Vocabulary: "Active" / "Paused"
              matches the audit's recommended copy. */}
          {schedule.enabled ? (
            <Pill sev="ok">Active</Pill>
          ) : (
            <Pill>Paused</Pill>
          )}
          {schedule.last_status ? (
            <Pill className={statusClass(schedule.last_status)}>
              {schedule.last_status}
            </Pill>
          ) : null}
        </div>
        <div className="text-[11.5px] text-muted-2 mt-0.5">
          Cron {formatCron(schedule.cron)} · Next{" "}
          {schedule.next_run_at
            ? new Date(schedule.next_run_at).toLocaleString()
            : "—"}
        </div>
      </div>
      <Button size="sm" variant="ghost" onClick={onRun} title="Run now">
        <Icon name="play" size={12} />
      </Button>
      {/* Stage 9 close-out: edit affordance. Pre-Stage-9 there was
          no way to fix a wrong job_args or cron value without
          deleting + recreating the schedule. Same form widgets as
          the create dialog so the edit experience matches. */}
      <Button
        size="sm"
        variant="ghost"
        onClick={onEdit}
        title="Edit schedule"
        aria-label="Edit schedule"
      >
        <Icon name="edit" size={12} />
      </Button>
      {/* Stage 8 audit fix (Issue 10): the toggle now spells out
          both state and action. "Pause" on an active row, "Activate"
          on a paused row. The old icon-only check/x toggle conflated
          current state with action because the operator had to know
          "check means enabled" to read it correctly. */}
      <Button
        size="sm"
        variant="ghost"
        onClick={onToggle}
        title={schedule.enabled ? "Pause this schedule" : "Activate this schedule"}
        aria-label={schedule.enabled ? "Pause schedule" : "Activate schedule"}
      >
        {schedule.enabled ? "Pause" : "Activate"}
      </Button>
      <Button size="sm" variant="ghost" onClick={onDelete} title="Delete">
        <Icon name="trash" size={12} />
      </Button>
    </div>
  );
}
