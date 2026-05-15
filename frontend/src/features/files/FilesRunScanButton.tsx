/**
 * Files "Run scan" split button (Stage 8 audit follow-up).
 *
 * Stage 3 introduced this component as a single-action button.
 * Stage 8 adds the "Scan all libraries" affordance — operators with
 * multiple libraries shouldn't have to pick one and click N times.
 *
 * Layout: a primary "Run scan" button (current library) + a chevron
 * that opens a small menu with "Scan all libraries". The chevron is
 * always enabled — scan-all doesn't require a selected library.
 *
 * Audit Issue (Stage 8 plan): "Per-library only. No 'scan all' affordance."
 */

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

export interface FilesRunScanButtonProps {
  libraryId: string;
  disabled: boolean;
  isPending: boolean;
  onRun: (id: string) => void;
  /**
   * Stage 8 (audit follow-up): scan-all callback. Triggered from the
   * dropdown menu under the chevron. The parent owns the mutation
   * (``useTriggerScanAll``) — this component is presentation-only.
   */
  onScanAll: () => void;
}

export function FilesRunScanButton({
  libraryId,
  disabled,
  isPending,
  onRun,
  onScanAll,
}: FilesRunScanButtonProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Close menu on outside-click or Escape.
  useEffect(() => {
    if (!menuOpen) return;
    function onDown(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    <div ref={wrapRef} className="relative inline-flex">
      <Button
        size="sm"
        variant="primary"
        disabled={disabled}
        onClick={() => libraryId && onRun(libraryId)}
        title={libraryId ? "Scan the selected library" : "Select a library first"}
        className="rounded-r-none"
      >
        <Icon name="play" size={12} />
        <span className="ml-1">{isPending ? "Scanning…" : "Run scan"}</span>
      </Button>
      <Button
        size="sm"
        variant="primary"
        // Chevron is always enabled — scan-all doesn't need a library
        // to be selected. We still disable while a previous mutation
        // is in flight to avoid double-fires.
        disabled={isPending}
        onClick={() => setMenuOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-label="More scan options"
        title="More scan options"
        className="rounded-l-none border-l border-l-[var(--accent-stroke,rgba(255,255,255,0.15))] px-1.5"
      >
        <Icon name="chev_down" size={12} />
      </Button>
      {menuOpen ? (
        <div
          role="menu"
          aria-label="Scan options"
          className={cn(
            "absolute right-0 top-full mt-1 z-30",
            "min-w-[200px] bg-surface border border-border rounded-md shadow-lg",
            "py-1",
          )}
        >
          <button
            type="button"
            role="menuitem"
            disabled={isPending}
            onClick={() => {
              setMenuOpen(false);
              onScanAll();
            }}
            className={cn(
              "w-full text-left px-3 py-1.5 text-[12.5px]",
              "hover:bg-[var(--hover)] disabled:opacity-50",
              "flex items-center gap-2",
            )}
          >
            <Icon name="play" size={12} />
            Scan all libraries
          </button>
        </div>
      ) : null}
    </div>
  );
}
