/**
 * Stage 3 — Files selection action bar.
 *
 * Extracted from the inline ``SelectionActions`` in ``FilesPage.tsx``.
 * Renders the contextual action row that appears in the toolbar when
 * one or more rows are selected: Re-evaluate · Optimize · Re-probe ·
 * Clear. Stage 27 originally added a Quarantine button here; Stage
 * 05 (v1.7) retired it along with the quarantine workflow.
 *
 * The bulk mutation hooks are called directly from this component so
 * the page hook (``useFilesPageState``) doesn't need to know about
 * them. Selection state itself remains in the page hook because it's
 * shared with the table.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  useBulkReevaluate,
  useBulkReprobe,
} from "@/hooks/useMedia";
import { toast } from "@/lib/toast";

import { FilesOptimizeProfilePicker } from "./FilesOptimizeProfilePicker";

export interface FilesSelectionActionsProps {
  count: number;
  selectedIds: Set<string>;
  onClear: () => void;
}

export function FilesSelectionActions({
  count,
  selectedIds,
  onClear,
}: FilesSelectionActionsProps) {
  const bulkReevaluate = useBulkReevaluate();
  // Stage 27: re-probe bulk mutation. The quarantine bulk
  // mutation that lived here pre-Stage-05 is gone (Section A.0).
  const bulkReprobe = useBulkReprobe();
  // Keep a local ``running`` flag for the optimize picker so the
  // button can show a spinner while the picker's mutation is in flight.
  const [optimizing, setOptimizing] = useState(false);

  async function runReevaluate() {
    try {
      const result = await bulkReevaluate.mutateAsync(Array.from(selectedIds));
      const missing = result.files_not_found.length;
      toast(
        missing > 0
          ? `Re-evaluated ${result.files_evaluated} files (${missing} not found)`
          : `Re-evaluated ${result.files_evaluated} file${
              result.files_evaluated === 1 ? "" : "s"
            }`,
        missing > 0 ? "warn" : "ok",
      );
      // Clear selection after success — the operator finished with
      // these files; carrying them across a refetch is more often
      // confusing than helpful.
      onClear();
    } catch (err) {
      toast(
        `Bulk re-evaluation failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  async function runReprobe() {
    try {
      const result = await bulkReprobe.mutateAsync(Array.from(selectedIds));
      // Compose a useful summary toast. The four buckets — reprobed
      // (clean success), failed (ffprobe couldn't read), orphaned
      // (file gone from disk), not-found (id wasn't in the database)
      // — give the operator enough signal to act on without dumping
      // a list of paths.
      const parts: string[] = [`${result.files_reprobed} re-probed`];
      if (result.files_failed > 0) parts.push(`${result.files_failed} failed`);
      if (result.files_orphaned > 0)
        parts.push(`${result.files_orphaned} orphaned`);
      if (result.files_not_found.length > 0)
        parts.push(`${result.files_not_found.length} not found`);
      const tone =
        result.files_failed > 0 || result.files_orphaned > 0 ? "warn" : "ok";
      toast(parts.join(", "), tone);
      onClear();
    } catch (err) {
      toast(
        `Bulk re-probe failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  // Stage 27's ``runQuarantine`` handler lived here. Stage 05
  // (v1.7) retired the quarantine workflow (Section A.0 — "delete
  // means delete"); the selection bar no longer offers a bulk
  // quarantine button. Operators who want to remove a selection
  // now write a rule with a Delete action.

  return (
    <div className="files-selection-bar">
      <span className="text-[12.5px] font-medium">{count} selected</span>
      <Button
        size="sm"
        variant="accent"
        onClick={runReevaluate}
        disabled={bulkReevaluate.isPending}
        title="Re-run enabled rules against the selected files. Updates severity and matched-rule lists."
      >
        <Icon name="refresh" size={12} />
        {bulkReevaluate.isPending ? "Re-evaluating…" : "Re-evaluate rules"}
      </Button>
      {/* Stage 28: bulk optimize. The button opens a profile-picker
          popover; choosing a profile enqueues the selected files
          against it. Falls back to a disabled state with a helpful
          title when no profiles are configured. */}
      <FilesOptimizeProfilePicker
        selectedIds={selectedIds}
        onSuccess={onClear}
        isRunning={optimizing}
        onRunningChange={setOptimizing}
      />
      {/* Stage 27: re-probe is wired through. The Stage 27
          quarantine Button that sat here has been removed — see
          ``runQuarantine`` comment above. */}
      <Button
        size="sm"
        onClick={runReprobe}
        disabled={bulkReprobe.isPending}
        title="Re-run ffprobe on the selected files. Updates codec / container metadata."
      >
        <Icon
          name="refresh"
          size={12}
          className={bulkReprobe.isPending ? "animate-spin" : undefined}
        />
        {bulkReprobe.isPending ? "Re-probing…" : "Re-probe"}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={onClear}
        title="Clear selection"
        aria-label="Clear selection"
      >
        <Icon name="x" size={12} />
      </Button>
    </div>
  );
}
