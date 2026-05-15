/**
 * Stage 3 — Files optimize-profile picker.
 *
 * Extracted from the inline ``OptimizeProfilePicker`` in
 * ``FilesPage.tsx``. Renders the "Optimize" button + popover that
 * enqueues selected files against an optimization profile.
 *
 * Stays as a custom popover instead of using the Stage 1 ``Modal`` or
 * Radix Popover because the existing test
 * (``FilesPage.stage28.test.tsx``) asserts on click-outside dismissal,
 * Escape dismissal, and the ``popover`` / ``popover-head`` /
 * ``popover-row`` CSS classes. A migration to Radix Popover is a Stage
 * 3b candidate after a visual baseline is captured.
 */

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  useBulkEnqueueOptimization,
  useOptimizationProfiles,
} from "@/hooks/useOptimization";
import { toast } from "@/lib/toast";

export interface FilesOptimizeProfilePickerProps {
  selectedIds: Set<string>;
  onSuccess: () => void;
  /** Lift the "currently enqueuing" state so the parent toolbar can
   *  reflect it on other action buttons if it wants to. */
  isRunning?: boolean;
  onRunningChange?: (running: boolean) => void;
}

export function FilesOptimizeProfilePicker({
  selectedIds,
  onSuccess,
  onRunningChange,
}: FilesOptimizeProfilePickerProps) {
  const profiles = useOptimizationProfiles();
  const bulk = useBulkEnqueueOptimization();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Close on click-outside. The popover is positioned relative to
  // the wrapping div via .popover's CSS — using a ref-based outside
  // detector rather than a global modal/backdrop keeps the
  // selection bar uncluttered (no full-screen overlay just for a
  // 200×N popover).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [open]);

  // Also close on Escape — keyboard parity with the rest of the
  // dialogs/drawers.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Notify parent of the pending mutation state so its layout doesn't
  // need to track this separately.
  useEffect(() => {
    onRunningChange?.(bulk.isPending);
  }, [bulk.isPending, onRunningChange]);

  // Only enabled profiles can receive bulk enqueues (server
  // rejects disabled ones with 422). Showing them in the picker
  // and surfacing the rejection at click-time would be a worse
  // UX than just hiding them — operators rarely want to enqueue
  // against a disabled profile by accident.
  const enabledProfiles = (profiles.data ?? []).filter((p) => p.enabled);

  async function onPick(profileName: string) {
    setOpen(false);
    try {
      const result = await bulk.mutateAsync({
        media_ids: Array.from(selectedIds),
        profile: profileName,
      });
      // Per-bucket toast. The four buckets (queued, already-
      // queued, skipped-active, not-found) tell different stories;
      // collapsing them into one number would hide useful signal.
      const parts: string[] = [];
      if (result.queued > 0) parts.push(`${result.queued} queued`);
      if (result.already_queued > 0)
        parts.push(`${result.already_queued} already queued`);
      if (result.skipped_active > 0)
        parts.push(`${result.skipped_active} skipped (in progress)`);
      if (result.files_not_found.length > 0)
        parts.push(`${result.files_not_found.length} not found`);
      const tone =
        result.skipped_active > 0 || result.files_not_found.length > 0
          ? "warn"
          : "ok";
      toast(parts.length > 0 ? parts.join(", ") : "Nothing to queue", tone);
      onSuccess();
    } catch (err) {
      toast(
        `Bulk optimize failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  // Three button states:
  //   - loading: profiles query is still resolving — show a
  //     disabled "Optimize" so the layout doesn't shift.
  //   - empty: no enabled profiles — disabled button with a
  //     tooltip pointing the operator at where to create one.
  //   - has profiles: a normal button that toggles the popover.
  if (profiles.isLoading) {
    return (
      <Button size="sm" disabled title="Loading profiles…">
        <Icon name="bolt" size={12} /> Optimize
      </Button>
    );
  }
  if (enabledProfiles.length === 0) {
    return (
      <Button
        size="sm"
        disabled
        title="No enabled optimization profiles — create one on the Optimization page first."
      >
        <Icon name="bolt" size={12} /> Optimize
      </Button>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      <Button
        size="sm"
        onClick={() => setOpen((v) => !v)}
        disabled={bulk.isPending}
        title="Queue the selected files against an optimization profile"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Icon
          name="bolt"
          size={12}
          className={bulk.isPending ? "animate-spin" : undefined}
        />
        {bulk.isPending ? "Queueing…" : "Optimize"}
      </Button>
      {open ? (
        <div className="popover" role="menu" aria-label="Optimization profiles">
          <div className="popover-head">Pick a profile</div>
          {enabledProfiles.map((p) => (
            <button
              key={p.id}
              type="button"
              role="menuitem"
              className="popover-row text-left"
              onClick={() => onPick(p.name)}
            >
              <Icon name="bolt" size={12} className="text-muted-2" />
              <div className="flex-1 min-w-0">
                <div className="text-[12.5px] truncate">{p.name}</div>
                {p.description ? (
                  <div className="text-[11px] text-muted-2 truncate">
                    {p.description}
                  </div>
                ) : null}
              </div>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
