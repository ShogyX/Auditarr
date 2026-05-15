import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { useWebSocketEvents } from "@/hooks/useWebSocketEvents";
import { invalidateRelated } from "@/lib/invalidate";

interface ScanProgress {
  /** ID of the currently-running scan, if any. */
  runId: string | null;
  /** Library the running scan belongs to. */
  libraryId: string | null;
  /** Latest counter snapshot. Updates from both ``scan.progress`` and
   *  ``scan.completed`` events. */
  filesSeen: number;
  /**
   * Stage 8 (audit follow-up): upper-bound count from
   * ``scan.progress`` events. Used by the progress bar to compute a
   * percent. ``null`` when the scanner hasn't enumerated yet — the
   * bar shows an indeterminate state in that case.
   */
  filesTotalEstimate: number | null;
  /**
   * Stage 8 (audit follow-up): integer 0..100 derived from
   * ``filesSeen`` / ``filesTotalEstimate``. ``null`` when no scan
   * is running or no total estimate is known. The progress bar
   * component uses this to set width.
   */
  percent: number | null;
  /** Whether a scan finished within the last few seconds. */
  recentlyCompleted: boolean;
}

const INITIAL: ScanProgress = {
  runId: null,
  libraryId: null,
  filesSeen: 0,
  filesTotalEstimate: null,
  percent: null,
  recentlyCompleted: false,
};

/**
 * Listen to scanner events on the WS bus.
 *
 * - Refreshes the libraries / media / scans queries when relevant events fire.
 * - Surfaces lightweight progress state for any UI that wants to show a
 *   "scan running" badge without subscribing to the WS client itself.
 *
 * Stage 8 (audit follow-up): now handles ``scan.progress`` events with
 * ``files_total_estimate`` so the UI can show a real percent bar
 * rather than just a "scanning…" pill.
 *
 * Uses the central ``invalidateRelated`` helper (see
 * ``frontend/src/lib/invalidate.ts``) so a scan completion refreshes
 * the full dependency graph — dashboard tiles, sidebar badges,
 * notifications — not just the few query keys this file used to list
 * by hand.
 */
export function useScanProgress(): ScanProgress {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<ScanProgress>(INITIAL);

  useWebSocketEvents((event) => {
    switch (event.name) {
      case "scan.started": {
        const data = event.payload as { run_id?: string; library_id?: string };
        setProgress({
          runId: data.run_id ?? null,
          libraryId: data.library_id ?? null,
          filesSeen: 0,
          filesTotalEstimate: null,
          percent: null,
          recentlyCompleted: false,
        });
        invalidateRelated(queryClient, "scan");
        return;
      }
      case "scan.progress": {
        // Stage 8 (audit follow-up): periodic snapshot from the
        // scanner. Update both seen + total so the bar advances.
        const data = event.payload as {
          run_id?: string;
          library_id?: string;
          files_seen?: number;
          files_total_estimate?: number;
        };
        setProgress((prev) => {
          const seen = data.files_seen ?? prev.filesSeen;
          const total =
            data.files_total_estimate ?? prev.filesTotalEstimate ?? null;
          // Percent is integer-rounded; cap at 99 until we get the
          // scan.completed event, so the bar never hits 100% before
          // the run is finalized.
          let percent: number | null;
          if (total && total > 0) {
            const raw = Math.floor((seen / total) * 100);
            percent = Math.max(0, Math.min(99, raw));
          } else {
            percent = null;
          }
          return {
            ...prev,
            runId: data.run_id ?? prev.runId,
            libraryId: data.library_id ?? prev.libraryId,
            filesSeen: seen,
            filesTotalEstimate: total,
            percent,
          };
        });
        return;
      }
      case "scan.completed": {
        const data = event.payload as {
          run_id?: string;
          library_id?: string;
          files_seen?: number;
        };
        setProgress({
          runId: data.run_id ?? null,
          libraryId: data.library_id ?? null,
          filesSeen: data.files_seen ?? 0,
          filesTotalEstimate: data.files_seen ?? null,
          percent: 100,
          recentlyCompleted: true,
        });
        invalidateRelated(queryClient, "scan");
        // Drop the "recently completed" flag after a beat so badges fade.
        setTimeout(
          () =>
            setProgress((p) => ({
              ...p,
              recentlyCompleted: false,
              // Also reset progress fields so the next mount/scan
              // starts from a clean state rather than showing 100%.
              percent: null,
              filesTotalEstimate: null,
            })),
          5000,
        );
        return;
      }
      case "scan.failed": {
        setProgress(INITIAL);
        invalidateRelated(queryClient, "scan");
        return;
      }
      case "media.added":
      case "media.updated":
      case "media.deleted":
        // Server-pushed media changes — refresh everything that
        // reads from the media namespace.
        invalidateRelated(queryClient, "media");
        return;
      default:
        return;
    }
  });

  // Reset progress on unmount so leftovers don't leak into the next mount.
  useEffect(() => () => setProgress(INITIAL), []);

  return progress;
}
