/**
 * v1.8.1: error banner for scan-trigger failures.
 *
 * The toast helper in `useTriggerScan`'s onError handles
 * transient feedback. This banner stays visible in the page
 * surface for the dominant failure mode — a 409 Conflict
 * because a stuck queued/running ScanRun row is blocking new
 * scans — and offers the operator an "Unstick library" action
 * that calls the new ``POST /scans/libraries/{id}/reset``
 * endpoint.
 *
 * The banner only renders when:
 *   - the user has clicked Run Scan,
 *   - the most recent error was a 409 Conflict on this library,
 *   - the user hasn't dismissed it yet.
 *
 * The banner is purely presentational; the parent owns the
 * mutations.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { ApiError } from "@/services/apiClient";

export interface FilesScanErrorBannerProps {
  /** The most recent trigger error from useTriggerScan. */
  error: unknown;
  /** The currently-selected library id; the reset action targets this. */
  libraryId: string;
  /** Whether the reset mutation is currently in flight. */
  resetting: boolean;
  /** Called when the operator clicks "Unstick library". */
  onReset: (libraryId: string) => void;
}

export function FilesScanErrorBanner({
  error,
  libraryId,
  resetting,
  onReset,
}: FilesScanErrorBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;
  if (!(error instanceof ApiError)) return null;
  // Only render for the stuck-scan case; other errors are
  // handled by the toast (which fires once and clears). A
  // persistent banner for a transient 500 would be noise.
  if (error.status !== 409) return null;
  if (!libraryId) return null;

  return (
    <div
      role="alert"
      className="flex items-center gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm"
    >
      <Icon name="alert" size={16} />
      <div className="flex-1">
        <div className="font-medium">A scan is already running for this library.</div>
        <div className="text-[12.5px] opacity-80 mt-0.5">
          {error.message}{" "}
          If the scan is stuck (worker crashed mid-run, container restarted),
          click <b>Unstick library</b> to clear it — you can then start a
          fresh scan.
        </div>
      </div>
      <Button
        size="sm"
        variant="default"
        disabled={resetting}
        onClick={() => onReset(libraryId)}
      >
        {resetting ? "Unsticking…" : "Unstick library"}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss"
      >
        <Icon name="x" size={12} />
      </Button>
    </div>
  );
}
