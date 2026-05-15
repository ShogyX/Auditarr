/**
 * Stage 10 audit fix (Issue 15) — Automation tab content.
 *
 * Extracted from the body of ``AutomationPage`` so the Rules page
 * can render the same operational surface as a tab (Custom /
 * Built-in / Suggestions / Automation). All Automation state +
 * queries + dialogs live here so neither parent owns automation
 * concerns.
 *
 * Two render contexts:
 *   - AutomationPage (the legacy /automation route; preserved for
 *     test-pages and BugHunt1 tests that still import it directly)
 *   - RulesPage when ``tab === "automation"``
 *
 * Stage 2 (audit follow-up): the three sub-cards (Schedules / Runs /
 * Queue) render unconditionally — including while ``jobKinds`` is
 * still loading. Only the "New schedule" affordance is disabled
 * during the kinds-load (you can't pick a kind you haven't fetched).
 *
 * Stage 2: the "New schedule" affordance now also lives in the
 * RulesPage header on the Automation tab. To let either parent open
 * the dialog without prop-drilling a shared context, this component
 * accepts an optional ``creating`` / ``onCreatingChange`` pair. When
 * the parent supplies them, the dialog is controlled by the parent
 * (URL-driven via ``?new=schedule`` in the RulesPage case). When the
 * parent omits them, the component falls back to local ``useState``
 * — that's the legacy ``/automation`` path used by smoke tests.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  useDeleteSchedule,
  useJobKinds,
  useJobRuns,
  useOptimizationQueue,
  useRunSchedule,
  useSchedules,
  useUpdateSchedule,
} from "@/hooks/useAutomation";

import { AutomationQueueCard } from "./AutomationQueueCard";
import { AutomationRunsCard } from "./AutomationRunsCard";
import { AutomationScheduleDialog } from "./AutomationScheduleDialog";
import { AutomationScheduleEditDialog } from "./AutomationScheduleEditDialog";
import { AutomationSchedulesCard } from "./AutomationSchedulesCard";

export interface AutomationTabContentProps {
  /** Controlled-mode: parent owns the dialog open state. */
  creating?: boolean;
  /** Controlled-mode: invoked when the dialog opens or closes. */
  onCreatingChange?: (next: boolean) => void;
  /**
   * Suppress the inline "New schedule" button at the top of this
   * body. The RulesPage hosts the button in its page header on the
   * Automation tab; suppressing the inline copy keeps the action
   * vocabulary single-sourced and avoids two identical buttons on
   * the same screen.
   */
  hideInlineNewScheduleButton?: boolean;
}

export function AutomationTabContent({
  creating: creatingProp,
  onCreatingChange,
  hideInlineNewScheduleButton = false,
}: AutomationTabContentProps = {}) {
  const schedules = useSchedules();
  const jobKinds = useJobKinds();
  const remove = useDeleteSchedule();
  const update = useUpdateSchedule();
  const run = useRunSchedule();
  const runs = useJobRuns({ limit: 20 });
  const queue = useOptimizationQueue();

  // Controlled vs uncontrolled. If the parent passed both props,
  // their value wins; otherwise we own the state locally.
  const [creatingLocal, setCreatingLocal] = useState(false);
  const isControlled =
    creatingProp !== undefined && onCreatingChange !== undefined;
  const creating = isControlled ? creatingProp : creatingLocal;
  const setCreating = isControlled ? onCreatingChange : setCreatingLocal;

  // Stage 9 close-out: which schedule (by id) the operator is
  // editing. Local-only state — there's no URL-driven equivalent
  // for edit; the edit affordance is per-row.
  const [editingId, setEditingId] = useState<string | null>(null);
  const editingSchedule =
    editingId && schedules.data
      ? schedules.data.find((s) => s.id === editingId) ?? null
      : null;

  return (
    <div className="flex flex-col gap-6">
      {hideInlineNewScheduleButton ? null : (
        <div className="flex items-center justify-end">
          <Button
            size="sm"
            variant="primary"
            onClick={() => setCreating(true)}
            disabled={jobKinds.isLoading || !jobKinds.data}
          >
            <Icon name="plus" size={12} />
            <span className="ml-1">New schedule</span>
          </Button>
        </div>
      )}

      {/* Sub-cards are deliberately unconditional. Each card renders
          its own internal loading state; hiding the whole card while
          another query is pending would leave operators staring at a
          blank page on every Automation-tab visit. */}
      <AutomationSchedulesCard
        schedules={schedules}
        update={update}
        remove={remove}
        run={run}
        onEdit={(id) => setEditingId(id)}
      />
      <AutomationRunsCard runs={runs} pageSize={20} />
      <AutomationQueueCard queue={queue} />

      {creating && jobKinds.data ? (
        <AutomationScheduleDialog
          jobKinds={jobKinds.data}
          onClose={() => setCreating(false)}
        />
      ) : null}
      {/* Stage 9 close-out: edit dialog. Only mounted when we have
          both a target schedule and the kinds vocabulary. */}
      {editingSchedule && jobKinds.data ? (
        <AutomationScheduleEditDialog
          schedule={editingSchedule}
          jobKinds={jobKinds.data}
          onClose={() => setEditingId(null)}
        />
      ) : null}
    </div>
  );
}
