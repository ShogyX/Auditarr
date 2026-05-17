/**
 * Stage 13 (plan §605) — scan progress lives in a Zustand store
 * rather than React component state.
 *
 * Pre-Stage-13: ``useScanProgress`` held the progress in
 * ``useState`` and reset it on unmount. That meant the operator
 * starting a scan from the dashboard, then navigating to /files
 * (or any other route that doesn't render the scan badge),
 * would lose the progress — even though the scan was still
 * running on the server. The next time they returned to a page
 * that subscribed to ``useScanProgress``, the bar started
 * fresh, missing the WS events that had fired while the badge
 * was unmounted.
 *
 * Stage 13 moves the state into this small store. Now:
 *   - A single ``useWebSocketEvents`` subscription updates the
 *     store from anywhere it's mounted (App-level mount in the
 *     RequireAuth shell guarantees it's always alive while the
 *     user is signed in).
 *   - Consumers read the slice they need via a selector. No
 *     unmount reset — the store outlives any individual
 *     component.
 *   - The shape is identical to the pre-Stage-13 ``ScanProgress``
 *     interface so consumers don't need to change.
 *
 * Plan §618 explicitly excludes persisting this across page
 * reloads — the WS reconnect on reload will re-emit the latest
 * progress event anyway. So we use a plain ``create``, not
 * ``persist``.
 */

import { create } from "zustand";

export interface ScanProgressState {
  /** ID of the currently-running scan, if any. */
  runId: string | null;
  /** Library the running scan belongs to. */
  libraryId: string | null;
  /** Latest counter snapshot. */
  filesSeen: number;
  /** Upper-bound count from ``scan.progress`` events. */
  filesTotalEstimate: number | null;
  /** Integer 0..100 derived from filesSeen / filesTotalEstimate. */
  percent: number | null;
  /** True for a brief window after a scan finishes (for badges). */
  recentlyCompleted: boolean;
}

interface ScanProgressActions {
  /** Replace the entire state with a new snapshot. */
  set: (next: ScanProgressState) => void;
  /** Apply a patch to the current state (functional update). */
  patch: (updater: (prev: ScanProgressState) => ScanProgressState) => void;
  /** Reset to the initial empty state. */
  reset: () => void;
}

const INITIAL: ScanProgressState = {
  runId: null,
  libraryId: null,
  filesSeen: 0,
  filesTotalEstimate: null,
  percent: null,
  recentlyCompleted: false,
};

export const useScanProgressStore = create<
  ScanProgressState & ScanProgressActions
>((set) => ({
  ...INITIAL,
  set: (next) => set(() => ({ ...next })),
  patch: (updater) =>
    set((prev) => {
      // Reconstruct the state slice without the action methods
      // so the updater operates on a pure data shape.
      const snapshot: ScanProgressState = {
        runId: prev.runId,
        libraryId: prev.libraryId,
        filesSeen: prev.filesSeen,
        filesTotalEstimate: prev.filesTotalEstimate,
        percent: prev.percent,
        recentlyCompleted: prev.recentlyCompleted,
      };
      return updater(snapshot);
    }),
  reset: () => set(() => ({ ...INITIAL })),
}));

/** Read-only initial state, exported for tests. */
export const SCAN_PROGRESS_INITIAL = INITIAL;
