/**
 * v1.9 Stage 2.4 — Delete confirmation dialog.
 *
 * Used by ``FilesSelectionActions`` (bulk delete) and
 * ``FileDetailDrawer`` (single delete). The two-mode UX:
 *
 *   * ``remove_from_disk=false`` (default, safe):
 *     - Title: "Remove from index"
 *     - One-click confirm; the file is left on disk and the next
 *       library scan will re-index it. The audit log captures the
 *       intent.
 *   * ``remove_from_disk=true`` (destructive):
 *     - Title: "Move files to trash"
 *     - The file (or selection) is moved into the date-bucketed
 *       trash dir. Recoverable but operator-visible. Requires the
 *       operator to type ``DELETE`` into the typed-confirmation
 *       phrase field before the action button enables.
 *
 * The dialog itself doesn't call the API — it surfaces choices and
 * invokes a callback. The parent owns the mutation hooks so toasts /
 * selection-clear can be wired in one place.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
} from "@/components/ui/Modal";
import { Pill } from "@/components/ui/Pill";

export interface DeleteFilesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** File names (or one name) shown in the body so the operator can
   *  confirm they're about to delete the right thing. */
  fileNames: string[];
  /** Severity of the selection, surfaced as a pill so the operator
   *  sees they're about to remove (e.g.) a critical file. ``null``
   *  hides the pill — appropriate for mixed-severity selections. */
  severityPreview?: "ok" | "info" | "warn" | "high" | "error" | "crit" | null;
  /** Fired when the operator confirms. The parent runs the mutation. */
  onConfirm: (args: { remove_from_disk: boolean; reason: string | null }) => void;
  /** True while the parent's mutation is in flight; disables the
   *  confirm button + the form controls. */
  isPending?: boolean;
}

const TYPED_CONFIRM_PHRASE = "DELETE";

export function DeleteFilesDialog({
  open,
  onOpenChange,
  fileNames,
  severityPreview = null,
  onConfirm,
  isPending = false,
}: DeleteFilesDialogProps) {
  const [removeFromDisk, setRemoveFromDisk] = useState(false);
  const [reason, setReason] = useState("");
  const [typedConfirm, setTypedConfirm] = useState("");

  const isBulk = fileNames.length > 1;
  const title = removeFromDisk
    ? isBulk
      ? `Move ${fileNames.length} files to trash`
      : "Move file to trash"
    : isBulk
      ? `Remove ${fileNames.length} files from index`
      : "Remove file from index";

  const typedConfirmOk =
    !removeFromDisk || typedConfirm.trim() === TYPED_CONFIRM_PHRASE;
  const canConfirm = typedConfirmOk && !isPending;

  function handleConfirm() {
    if (!canConfirm) return;
    onConfirm({
      remove_from_disk: removeFromDisk,
      reason: reason.trim() ? reason.trim() : null,
    });
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      // Reset local state on close so the next open starts clean.
      setRemoveFromDisk(false);
      setReason("");
      setTypedConfirm("");
    }
    onOpenChange(next);
  }

  return (
    <Modal
      open={open}
      onOpenChange={handleOpenChange}
      size="md"
      ariaLabel={title}
      ariaDescription={
        removeFromDisk
          ? "This will move the selected files to the trash directory. The action is recoverable but visible to all operators."
          : "This removes the database rows for the selected files. The files on disk are not touched."
      }
    >
      <ModalHead
        title={title}
        subtitle={
          isBulk
            ? `${fileNames.length} file${fileNames.length === 1 ? "" : "s"} selected`
            : fileNames[0]
        }
        onClose={() => handleOpenChange(false)}
      />
      <ModalBody>
        <div className="flex flex-col gap-3">
          {severityPreview ? (
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className="text-muted-2">Severity:</span>
              <Pill sev={severityPreview}>{severityPreview}</Pill>
            </div>
          ) : null}

          {fileNames.length > 1 ? (
            <details className="text-[12px] text-muted-2">
              <summary className="cursor-pointer">
                Show {fileNames.length} file names
              </summary>
              <ul className="mt-2 max-h-32 overflow-y-auto pl-4 list-disc">
                {fileNames.slice(0, 100).map((name) => (
                  <li key={name} className="truncate">
                    {name}
                  </li>
                ))}
                {fileNames.length > 100 ? (
                  <li className="italic">
                    …and {fileNames.length - 100} more
                  </li>
                ) : null}
              </ul>
            </details>
          ) : null}

          <label className="inline-flex items-center gap-2 text-[12.5px]">
            <input
              type="checkbox"
              checked={removeFromDisk}
              disabled={isPending}
              onChange={(e) => setRemoveFromDisk(e.target.checked)}
            />
            <span>
              Also remove from disk{" "}
              <span className="text-muted-2">
                (move to <code>data_dir/trash/</code>)
              </span>
            </span>
          </label>

          <label className="flex flex-col gap-1 text-[12.5px]">
            <span className="text-muted-2">Reason (optional)</span>
            <input
              type="text"
              value={reason}
              disabled={isPending}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Recorded in the audit log"
              className="px-2.5 h-8 rounded-[6px] bg-surface-sunk border border-border text-[12.5px]"
              maxLength={1000}
            />
          </label>

          {removeFromDisk ? (
            <label className="flex flex-col gap-1 text-[12.5px]">
              <span className="text-sev-warn">
                Type <code className="font-mono">{TYPED_CONFIRM_PHRASE}</code>{" "}
                to confirm
              </span>
              <input
                type="text"
                value={typedConfirm}
                disabled={isPending}
                onChange={(e) => setTypedConfirm(e.target.value)}
                placeholder={TYPED_CONFIRM_PHRASE}
                className="px-2.5 h-8 rounded-[6px] bg-surface-sunk border border-border text-[12.5px] font-mono"
                aria-label={`Type ${TYPED_CONFIRM_PHRASE} to confirm`}
              />
            </label>
          ) : null}
        </div>
      </ModalBody>
      <ModalFoot>
        <Button
          variant="ghost"
          onClick={() => handleOpenChange(false)}
          disabled={isPending}
        >
          Cancel
        </Button>
        <Button
          variant="danger"
          onClick={handleConfirm}
          disabled={!canConfirm}
          title={
            !typedConfirmOk
              ? `Type "${TYPED_CONFIRM_PHRASE}" to confirm`
              : undefined
          }
        >
          {isPending ? "Deleting…" : removeFromDisk ? "Move to trash" : "Remove"}
        </Button>
      </ModalFoot>
    </Modal>
  );
}
