/**
 * v1.9 Stage 2.6 — Factory reset confirmation dialog.
 *
 * Modeled after DeleteFilesDialog's typed-confirmation pattern but
 * with stricter copy and a stricter gate: the operator must type
 * the exact phrase ``reset auditarr`` (case-insensitive) before
 * the destructive button enables.
 *
 * The dialog itself doesn't call the API — it surfaces the
 * confirmation flow and invokes ``onConfirm(phrase)`` with the
 * exact phrase the operator typed. The parent owns the mutation
 * so toasts / cache-clear / navigation can live there.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
} from "@/components/ui/Modal";

export const FACTORY_RESET_PHRASE = "reset auditarr";

export interface FactoryResetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (phrase: string) => void;
  isPending?: boolean;
}

export function FactoryResetDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending = false,
}: FactoryResetDialogProps) {
  const [phrase, setPhrase] = useState("");
  const phraseOk = phrase.trim().toLowerCase() === FACTORY_RESET_PHRASE;
  const canConfirm = phraseOk && !isPending;

  function handleOpenChange(next: boolean) {
    if (!next) setPhrase("");
    onOpenChange(next);
  }

  return (
    <Modal
      open={open}
      onOpenChange={handleOpenChange}
      size="md"
      ariaLabel="Factory reset Auditarr"
      ariaDescription="This wipes the application back to a fresh-install state. Your admin account is preserved; everything else is removed."
    >
      <ModalHead
        title="Factory reset"
        subtitle="Wipe Auditarr back to a fresh-install state"
        onClose={() => handleOpenChange(false)}
      />
      <ModalBody>
        <div className="flex flex-col gap-3">
          <div
            className="text-[12.5px] p-3 rounded-md bg-sev-error/10 text-sev-error border border-sev-error/30"
            role="alert"
          >
            <div className="flex items-start gap-2">
              <Icon name="alert" size={13} className="mt-0.5 shrink-0" />
              <div>
                <strong>This cannot be undone.</strong> Every library,
                file index, rule, integration, optimization queue
                entry, playback record, scan run, and notification
                delivery will be removed. The ``data_dir/trash/``
                directory will be emptied. Your admin login is
                preserved so you can rebuild from a clean slate.
              </div>
            </div>
          </div>

          <ul className="text-[12px] text-muted-2 list-disc pl-5 space-y-1">
            <li>
              <strong>Preserved:</strong> your user account, the audit
              log, and Auditarr's schema-migration history.
            </li>
            <li>
              <strong>Wiped:</strong> every other table plus the
              on-disk trash directory.
            </li>
            <li>
              An audit-log entry is written so the reset itself is
              recorded.
            </li>
          </ul>

          <label className="flex flex-col gap-1 text-[12.5px]">
            <span className="text-sev-error">
              Type <code className="font-mono">{FACTORY_RESET_PHRASE}</code>{" "}
              exactly to confirm
            </span>
            <input
              type="text"
              value={phrase}
              disabled={isPending}
              onChange={(e) => setPhrase(e.target.value)}
              placeholder={FACTORY_RESET_PHRASE}
              className="px-2.5 h-8 rounded-[6px] bg-surface-sunk border border-border text-[12.5px] font-mono"
              aria-label={`Type ${FACTORY_RESET_PHRASE} to confirm`}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
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
          onClick={() => canConfirm && onConfirm(phrase.trim())}
          disabled={!canConfirm}
          title={
            !phraseOk
              ? `Type "${FACTORY_RESET_PHRASE}" to confirm`
              : undefined
          }
        >
          {isPending ? "Resetting…" : "Factory reset"}
        </Button>
      </ModalFoot>
    </Modal>
  );
}
