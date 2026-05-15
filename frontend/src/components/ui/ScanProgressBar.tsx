/**
 * Real-time scan progress bar (Stage 8 audit follow-up).
 *
 * Renders a horizontal progress bar driven by ``useScanProgress``.
 * Shown in the FilesPage header and the DashboardPage header so the
 * operator gets a visible signal that a long scan is in progress
 * (the pre-Stage-8 affordance was a single "Scanning…" pill).
 *
 * Three visual states:
 *   - No scan running → renders nothing (caller may still gate on
 *     ``runId !== null`` to avoid empty wrapper space).
 *   - Scan running, no total estimate yet → indeterminate striped
 *     bar (the scanner is still walking ``_enumerate``).
 *   - Scan running with progress → solid bar at ``percent%``,
 *     count + total displayed inline.
 *
 * The component is intentionally header-friendly: 4px tall, no
 * Card wrapper, no internal padding. The container decides where
 * to place it.
 */

import { Pill } from "@/components/ui/Pill";
import { cn } from "@/lib/cn";
import { useScanProgress } from "@/hooks/useScanProgress";

export interface ScanProgressBarProps {
  /** Optional className for the outer wrapper. */
  className?: string;
  /** When true, render nothing while no scan is running. Default true. */
  hideWhenIdle?: boolean;
}

export function ScanProgressBar({
  className,
  hideWhenIdle = true,
}: ScanProgressBarProps) {
  const progress = useScanProgress();

  // Nothing to show: no run and the operator wants us hidden.
  if (hideWhenIdle && !progress.runId && !progress.recentlyCompleted) {
    return null;
  }

  // Determine the visual state.
  const hasTotal =
    progress.filesTotalEstimate !== null &&
    progress.filesTotalEstimate > 0;
  const percent = progress.percent ?? 0;
  const indeterminate = progress.runId !== null && !hasTotal;

  return (
    <div
      className={cn("flex items-center gap-3", className)}
      role="status"
      aria-live="polite"
      aria-label={
        progress.runId
          ? `Scanning ${progress.filesSeen} of ${progress.filesTotalEstimate ?? "?"} files`
          : "Scan completed"
      }
    >
      {/* Status pill on the left so the operator sees the verb. */}
      {progress.recentlyCompleted ? (
        <Pill sev="ok">Scan complete</Pill>
      ) : indeterminate ? (
        <Pill>Enumerating…</Pill>
      ) : (
        <Pill sev="info">Scanning</Pill>
      )}

      {/* The bar itself. Width matches the parent. */}
      <div
        className={cn(
          "relative h-1 flex-1 rounded-full overflow-hidden",
          "bg-surface-sunk",
        )}
        data-progress={progress.runId ? "active" : "idle"}
        data-percent={progress.percent ?? "-"}
      >
        {indeterminate ? (
          // Indeterminate state — a 30%-wide bar that slides via the
          // `scan-progress-indeterminate` keyframe in the global
          // stylesheet. If the keyframe isn't defined the bar still
          // shows a static slice so the operator sees activity.
          <div
            className="absolute inset-y-0 w-[30%] bg-accent"
            style={{
              animation:
                "scan-progress-indeterminate 1.4s ease-in-out infinite",
            }}
          />
        ) : (
          <div
            className="absolute inset-y-0 left-0 bg-accent transition-all"
            style={{ width: `${percent}%` }}
          />
        )}
      </div>

      {/* Counter text on the right. */}
      <span className="text-[11.5px] text-muted-2 tabular-nums">
        {progress.filesTotalEstimate
          ? `${progress.filesSeen.toLocaleString()} / ${progress.filesTotalEstimate.toLocaleString()}`
          : progress.filesSeen
            ? progress.filesSeen.toLocaleString()
            : ""}
      </span>
    </div>
  );
}
