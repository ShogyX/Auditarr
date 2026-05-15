/**
 * Stage 2 — Runtime settings apply confirmation.
 *
 * Adopts the Stage 1 ``Modal`` primitive (was ``.dialog-*`` CSS).
 *
 * Renders a per-row diff (label / before / after / "now" or "next
 * tick" or "clear") plus a per-field warning block when any field
 * carries ``requires_warning``. The diff row uses the ``clear`` pill
 * when the apply will result in a DELETE (going to env default with
 * an existing override) — the test asserts on this pill.
 *
 * Stage 2 addition: if ANY of the queued changes targets a field
 * marked ``sensitivity === "elevated"``, a Re-type-to-confirm step
 * appears before the Apply button is enabled. No current spec is
 * elevated; the gate is dormant on today's data. Future-proofs the
 * UI for when a real elevated entry lands.
 *
 * Test contract preserved:
 *   - ``role="dialog"`` (from Modal/Dialog.Content)
 *   - diff rows visible in the dialog
 *   - "clear" pill on going-to-default rows
 *   - "Apply changes" confirm button label
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Modal, ModalBody, ModalFoot, ModalHead } from "@/components/ui/Modal";
import type { RuntimeField } from "@/hooks/useRuntimeSettings";

import { sameValue, type EditValue, type Edits } from "./runtimeSettingsShared";

export interface RuntimeConfirmApplyDialogProps {
  dirtyKeys: string[];
  edits: Edits;
  fields: RuntimeField[];
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
}

interface Row {
  key: string;
  label: string;
  before: unknown;
  after: EditValue | undefined;
  impact: "immediate" | "next_tick";
  warning: string | null;
  sensitivity: "normal" | "elevated";
  goingToDefault: boolean;
}

export function RuntimeConfirmApplyDialog({
  dirtyKeys,
  edits,
  fields,
  onCancel,
  onConfirm,
  busy,
}: RuntimeConfirmApplyDialogProps) {
  // Stage 2: elevated-confirm gate. When any pending change targets
  // an elevated-sensitivity field, the operator must type the literal
  // string "CONFIRM" into a confirmation input before Apply enables.
  // This is a soft gate (the backend doesn't enforce it) — its sole
  // purpose is preventing an accidental click on a destructive
  // change.
  const [confirmText, setConfirmText] = useState("");

  const rows: Row[] = [];
  for (const k of dirtyKeys) {
    const f = fields.find((x) => x.key === k);
    if (!f) continue;
    rows.push({
      key: k,
      label: f.label,
      before: f.value,
      after: edits[k],
      impact: f.impact,
      warning: f.requires_warning,
      sensitivity: f.sensitivity,
      goingToDefault:
        sameValue(edits[k], f.env_default) && f.is_override,
    });
  }

  const warnings = rows.filter((r) => r.warning);
  const elevated = rows.some((r) => r.sensitivity === "elevated");
  const elevatedConfirmed = !elevated || confirmText === "CONFIRM";

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onCancel()}
      ariaLabel="Apply runtime changes"
      size="lg"
    >
      <ModalHead title="Apply runtime changes" onClose={onCancel} />
      <ModalBody className="flex flex-col gap-3">
        {warnings.length > 0 ? (
          <div className="runtime-warn">
            <Icon
              name="alert"
              size={14}
              className="text-sev-warn shrink-0 mt-0.5"
            />
            <div>
              <div className="font-semibold text-[13px]">
                {warnings.length} change
                {warnings.length === 1 ? "" : "s"} need confirmation
              </div>
              <ul className="m-0 pl-4 mt-1 text-[12.5px] leading-snug">
                {warnings.map((w) => (
                  <li key={w.key}>
                    <strong>{w.label}.</strong> {w.warning}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        <div className="diff-table">
          <div className="diff-head">
            <span>Setting</span>
            <span>Before</span>
            <span>After</span>
            <span>Apply</span>
          </div>
          {rows.map((row) => (
            <div key={row.key} className="diff-row">
              <div className="min-w-0">
                <div className="text-[13px] font-medium truncate">
                  {row.label}
                </div>
                <code className="font-mono text-[11px] text-muted-2">
                  {row.key}
                </code>
              </div>
              <code className="diff-cell">{String(row.before)}</code>
              <code className="diff-cell-after">{String(row.after)}</code>
              <span className="pill">
                {row.goingToDefault
                  ? "clear"
                  : row.impact === "immediate"
                    ? "now"
                    : "next tick"}
              </span>
            </div>
          ))}
        </div>

        {elevated ? (
          <div className="runtime-warn">
            <Icon
              name="alert"
              size={14}
              className="text-sev-warn shrink-0 mt-0.5"
            />
            <div className="flex-1">
              <div className="font-semibold text-[13px] mb-1">
                Elevated-sensitivity confirmation
              </div>
              <p className="text-[12.5px] m-0 mb-2">
                One or more pending changes target an elevated-
                sensitivity setting. Type <strong>CONFIRM</strong> to
                enable Apply.
              </p>
              <Input
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="CONFIRM"
                aria-label="Type CONFIRM to enable Apply"
                style={{ width: 180 }}
              />
            </div>
          </div>
        ) : null}
      </ModalBody>
      <ModalFoot>
        <Button size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="accent"
          onClick={onConfirm}
          disabled={busy || !elevatedConfirmed}
        >
          {busy ? "Applying…" : "Apply changes"}
        </Button>
      </ModalFoot>
    </Modal>
  );
}
