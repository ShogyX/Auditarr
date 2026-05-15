/**
 * Stage 2 — Runtime settings save bar.
 *
 * Extracted from the inline save bar in RuntimeSettingsPanel. Shows
 * "N change(s) pending" + an impact breakdown ("X immediate · Y at
 * next tick"), plus Discard/Apply actions.
 *
 * Test contract preserved:
 *   - text matches /1 change pending/i, /2 changes pending/i, etc.
 *   - the "immediate" count is rendered with the substring "immediate"
 *   - clicking Apply opens the parent's confirm dialog
 */

import { Button } from "@/components/ui/Button";

export interface RuntimeSaveBarProps {
  pendingCount: number;
  immediateCount: number;
  nextTickCount: number;
  onDiscardAll: () => void;
  onApply: () => void;
  busy: boolean;
}

export function RuntimeSaveBar({
  pendingCount,
  immediateCount,
  nextTickCount,
  onDiscardAll,
  onApply,
  busy,
}: RuntimeSaveBarProps) {
  if (pendingCount === 0) return null;
  return (
    <div className="runtime-savebar">
      <span className="text-[13px] font-medium">
        {pendingCount} change{pendingCount === 1 ? "" : "s"} pending
      </span>
      <span className="text-[12px] text-muted">
        {immediateCount > 0 ? <>{immediateCount} immediate</> : null}
        {immediateCount > 0 && nextTickCount > 0 ? " · " : null}
        {nextTickCount > 0 ? (
          <>{nextTickCount} apply at next tick</>
        ) : null}
      </span>
      <span className="flex-1" />
      <Button size="sm" onClick={onDiscardAll}>
        Discard all
      </Button>
      <Button size="sm" variant="accent" onClick={onApply} disabled={busy}>
        Apply {pendingCount} change{pendingCount === 1 ? "" : "s"}
      </Button>
    </div>
  );
}
