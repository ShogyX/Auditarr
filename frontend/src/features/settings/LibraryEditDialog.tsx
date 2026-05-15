/**
 * Library edit dialog (Stage 5 audit follow-up).
 *
 * The pre-Stage-5 Libraries card on the Settings page exposed only
 * enable/disable + run-scan + delete. Updating a library's name,
 * root path, kind, or scan interval was impossible from the UI even
 * though ``PATCH /libraries/{id}`` already supported every field.
 *
 * This dialog plugs that gap. Audit Issue 6.
 *
 * Behaviour:
 *   - Modal opens from an "Edit" button on each library row.
 *   - All four mutable fields are shown with current values.
 *   - Save calls ``useUpdateLibrary().mutateAsync`` with only the
 *     fields the operator actually changed (dirty-tracking) so we
 *     don't trip server-side "no change" validators.
 *   - Cancel discards the draft.
 *
 * The dialog is intentionally minimal — no fancy autocomplete on
 * root_path, no validation of disk presence. That kind of polish
 * belongs in a follow-up; right now the gap is that the field
 * can't be edited at ALL.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Modal, ModalBody, ModalFoot, ModalHead } from "@/components/ui/Modal";
import { useUpdateLibrary } from "@/hooks/useMedia";
import type { Library } from "@/hooks/useMedia";
import { ApiError } from "@/services/apiClient";
import { toast } from "@/lib/toast";

export interface LibraryEditDialogProps {
  library: Library | null;
  onOpenChange: (open: boolean) => void;
}

type Kind = "movies" | "tv" | "music" | "mixed";

export function LibraryEditDialog({
  library,
  onOpenChange,
}: LibraryEditDialogProps) {
  const update = useUpdateLibrary();
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [kind, setKind] = useState<Kind>("movies");
  const [scanIntervalMinutes, setScanIntervalMinutes] = useState<number>(0);

  // Sync inputs whenever the dialog opens with a new library.
  useEffect(() => {
    if (library) {
      setName(library.name);
      setRootPath(library.root_path);
      setKind((library.kind as Kind) ?? "movies");
      setScanIntervalMinutes(library.scan_interval_minutes ?? 0);
    }
  }, [library]);

  if (!library) {
    return null;
  }

  function close() {
    onOpenChange(false);
  }

  async function onSave() {
    if (!library) return;
    // Only send changed fields — keeps the diff minimal and
    // matches the backend's "no-op if no fields supplied" semantic.
    const patch: Record<string, unknown> = {};
    if (name.trim() && name !== library.name) patch.name = name.trim();
    if (rootPath.trim() && rootPath !== library.root_path) {
      patch.root_path = rootPath.trim();
    }
    if (kind !== library.kind) patch.kind = kind;
    if (
      scanIntervalMinutes !== (library.scan_interval_minutes ?? 0)
    ) {
      patch.scan_interval_minutes = scanIntervalMinutes;
    }

    if (Object.keys(patch).length === 0) {
      toast("Nothing to save — no fields changed.", "warn");
      return;
    }

    try {
      await update.mutateAsync({ id: library.id, patch });
      toast(`Updated library "${library.name}"`, "ok");
      close();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Update failed: ${msg}`, "error", 5000);
    }
  }

  return (
    <Modal
      open={library !== null}
      onOpenChange={onOpenChange}
      ariaLabel={`Edit library ${library.name}`}
      size="md"
    >
      <ModalHead
        title="Edit library"
        subtitle={`Update the name, root path, kind, or scan interval for "${library.name}".`}
        onClose={close}
      />
      <ModalBody>
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-[12px]">
            Name
            <input
              className="settings-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px]">
            Root path
            <input
              className="settings-input mono"
              value={rootPath}
              onChange={(e) => setRootPath(e.target.value)}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px]">
            Kind
            <select
              className="settings-input"
              value={kind}
              onChange={(e) => setKind(e.target.value as Kind)}
            >
              <option value="movies">Movies</option>
              <option value="tv">TV</option>
              <option value="music">Music</option>
              <option value="mixed">Mixed</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[12px]">
            Scan interval (minutes)
            <input
              className="settings-input"
              type="number"
              min={0}
              max={10080}
              value={scanIntervalMinutes}
              onChange={(e) =>
                setScanIntervalMinutes(
                  Math.max(0, Math.floor(Number(e.target.value) || 0)),
                )
              }
            />
            <span className="text-[11px] text-muted-2">
              0 disables automatic scans for this library.
            </span>
          </label>
        </div>
      </ModalBody>
      <ModalFoot>
        <span className="flex-1" />
        <Button variant="ghost" onClick={close} disabled={update.isPending}>
          Cancel
        </Button>
        <Button
          variant="accent"
          onClick={onSave}
          disabled={update.isPending}
        >
          {update.isPending ? "Saving…" : "Save changes"}
        </Button>
      </ModalFoot>
    </Modal>
  );
}
