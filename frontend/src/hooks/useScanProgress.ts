/**
 * Stage 8 (audit follow-up) + Stage 13 (plan §605, §616) —
 * scan progress hooks.
 *
 * Two hooks:
 *   1. ``useScanProgressWs()`` — subscribes to the WS bus and
 *      mutates the central :mod:`scanProgressStore` as
 *      ``scan.*`` events fire. Mount this ONCE at the
 *      app-shell level so the store is always being fed
 *      regardless of which page the user is on.
 *   2. ``useScanProgress()`` — reads the current snapshot
 *      from the store. Drop-in replacement for the pre-
 *      Stage-13 version that held local state. Returns the
 *      same ``ScanProgress`` shape so existing consumers
 *      don't need to change.
 *
 * The split fixes the plan §616 "scan progress bar doesn't
 * disappear when navigating to Files and back" requirement:
 * pre-Stage-13 the bar's state lived inside the badge
 * component, so navigating away unmounted it and reset
 * progress to zero. Now the WS subscription lives at the shell
 * level (above route mounts) and the store outlives any
 * individual route.
 */

import { useQueryClient } from "@tanstack/react-query";

import { useWebSocketEvents } from "@/hooks/useWebSocketEvents";
import { invalidateRelated } from "@/lib/invalidate";
import {
  type ScanProgressState,
  useScanProgressStore,
} from "@/stores/scanProgressStore";

/** Pre-Stage-13 public type — re-exported for compatibility. */
export type ScanProgress = ScanProgressState;

/**
 * App-shell-level subscription: pumps scan events from the WS
 * bus into the central :mod:`scanProgressStore`. Mount ONCE.
 */
export function useScanProgressWs(): void {
  const queryClient = useQueryClient();
  const setProgress = useScanProgressStore((s) => s.set);
  const patchProgress = useScanProgressStore((s) => s.patch);

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
        const data = event.payload as {
          run_id?: string;
          library_id?: string;
          files_seen?: number;
          files_total_estimate?: number;
        };
        patchProgress((prev) => {
          const seen = data.files_seen ?? prev.filesSeen;
          const total =
            data.files_total_estimate ?? prev.filesTotalEstimate ?? null;
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
        // Drop the "recently completed" flag after a beat
        // so badges fade. Direct timer is fine; the store
        // outlives the component so this can't fire after
        // an unmount.
        setTimeout(() => {
          patchProgress((p) => ({
            ...p,
            recentlyCompleted: false,
            percent: null,
            filesTotalEstimate: null,
          }));
        }, 5000);
        return;
      }
      case "scan.failed": {
        useScanProgressStore.getState().reset();
        invalidateRelated(queryClient, "scan");
        return;
      }
      case "media.added":
      case "media.updated":
      case "media.deleted":
        invalidateRelated(queryClient, "media");
        return;
      default:
        return;
    }
  });
}

/**
 * Read-only access to the current scan progress snapshot.
 * Pure selector — does NOT subscribe to the WS bus; the
 * subscription happens once in :func:`useScanProgressWs`
 * mounted at the app shell.
 *
 * Implementation note: each call selects the individual
 * fields rather than constructing an object inside the
 * selector. A naive ``s => ({ ...subset })`` selector
 * returns a new reference every render, which Zustand
 * interprets as a state change and triggers an infinite
 * re-render loop. Reading scalars directly avoids that
 * trap; React's bail-out skips re-render when each scalar
 * is unchanged.
 */
export function useScanProgress(): ScanProgress {
  const runId = useScanProgressStore((s) => s.runId);
  const libraryId = useScanProgressStore((s) => s.libraryId);
  const filesSeen = useScanProgressStore((s) => s.filesSeen);
  const filesTotalEstimate = useScanProgressStore(
    (s) => s.filesTotalEstimate,
  );
  const percent = useScanProgressStore((s) => s.percent);
  const recentlyCompleted = useScanProgressStore(
    (s) => s.recentlyCompleted,
  );
  return {
    runId,
    libraryId,
    filesSeen,
    filesTotalEstimate,
    percent,
    recentlyCompleted,
  };
}
