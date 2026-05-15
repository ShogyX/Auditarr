/**
 * Stage 6 — Plugin uninstall confirmation dialog.
 *
 * Adopts the Stage 1 ``Modal`` primitive. The previous implementation
 * used the ``.dialog-*`` CSS family from Stage 22 — that CSS still
 * works, but it's a third overlay vocabulary alongside the
 * ``fixed inset-0`` style and ``Modal``. Stage 6 consolidates on
 * ``Modal`` so the Auditarr UI has one canonical modal pattern.
 *
 * Test contract preserved:
 *   - ``role="dialog"`` (from Radix Dialog.Content)
 *   - accessible name matches ``/uninstall ${plugin.name}/i`` (the
 *     ``ariaLabel`` is passed to ``Modal`` and Radix exposes it via
 *     ``aria-label``; ``aria-labelledby`` also points at the sr-only
 *     Dialog.Title which carries the same text).
 *   - ``aria-modal="true"`` (set explicitly on Modal in Stage 5)
 *
 * Focus order is preserved: ``Cancel`` keeps its ``autoFocus`` so an
 * accidental Enter doesn't auto-confirm the destructive action.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
} from "@/components/ui/Modal";
import type { PluginSummary } from "@/hooks/usePlugins";

export interface UninstallConfirmDialogProps {
  plugin: PluginSummary;
  isPending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function UninstallConfirmDialog({
  plugin,
  isPending,
  onConfirm,
  onClose,
}: UninstallConfirmDialogProps) {
  const title = `Uninstall ${plugin.name}?`;
  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      ariaLabel={title}
      size="sm"
    >
      <ModalHead title={title} onClose={onClose} />
      <ModalBody>
        <p className="text-[13px] m-0 mb-2">
          This will run the plugin's shutdown hooks and{" "}
          <strong>delete its files</strong> from disk. The plugin's stored
          settings will remain in the database — re-installing the plugin
          later will pick them up automatically.
        </p>
        {plugin.routes ? (
          <p className="text-[12px] text-muted-2 m-0">
            <strong>Note:</strong> this plugin registers HTTP routes. The
            routes cannot be unmounted at runtime; they will return errors
            after uninstall until you restart Auditarr.
          </p>
        ) : null}
      </ModalBody>
      <ModalFoot>
        {/* Focus order: Cancel first so it's the default destination
            of an accidental Enter keypress. */}
        <Button
          size="sm"
          variant="ghost"
          onClick={onClose}
          disabled={isPending}
          autoFocus
        >
          Cancel
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={onConfirm}
          disabled={isPending}
        >
          <Icon name="trash" size={12} />
          <span className="ml-1">
            {isPending ? "Uninstalling…" : "Uninstall"}
          </span>
        </Button>
      </ModalFoot>
    </Modal>
  );
}
