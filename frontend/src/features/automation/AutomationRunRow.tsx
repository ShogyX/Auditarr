/**
 * Stage 5 — Automation job-run row.
 *
 * Extracted from the inline ``RunRow`` in ``AutomationPage.tsx``.
 * Renders one item from the Recent runs list: job kind + trigger
 * source + duration + optional error tail.
 */

import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import type { JobRun } from "@/hooks/useAutomation";

import { formatDuration, statusClass } from "./automationShared";

export interface AutomationRunRowProps {
  run: JobRun;
}

export function AutomationRunRow({ run }: AutomationRunRowProps) {
  return (
    <div className="px-4 py-2 border-b border-border last:border-b-0 flex items-center gap-3">
      <Icon name="clock" size={14} className="text-muted-2 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[12.5px] font-medium truncate">
            {run.job_kind}
          </span>
          <Tag>{run.trigger}</Tag>
        </div>
        <div className="text-[11px] text-muted-2 mt-0.5 truncate">
          {new Date(run.started_at).toLocaleString()} ·{" "}
          {formatDuration(run.duration_ms)}
          {run.error ? ` · ${run.error}` : null}
        </div>
      </div>
      <Pill className={statusClass(run.status)}>{run.status}</Pill>
    </div>
  );
}
