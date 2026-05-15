/**
 * Stage 14 (audit follow-up) — per-scan detail page.
 *
 * Backs the new ``/scans/:scanId`` route. Triggered from the
 * Dashboard's "Recent scans" card by clicking a row. Renders:
 *
 *   - Status pill + mode + started/finished timestamps.
 *   - Counter grid: files seen / added / updated / orphaned / probe failures.
 *   - On ``status === "failed"``: the ``error`` field surfaced
 *     prominently in a ``<pre>`` block.
 *   - Link to "Files in this library" via the deep-link
 *     ``/files?library_id=...``.
 *
 * Per the Stage 14 guard rail, the snapshot does NOT auto-refresh
 * on websocket events — operators read this view to understand
 * what already happened, and watching it mutate under their feet
 * would be more confusing than helpful.
 */

import { Link, useNavigate, useParams } from "react-router-dom";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { useScanDetail } from "@/hooks/useMedia";

function statusSev(status: string): "ok" | "warn" | "error" | undefined {
  switch (status) {
    case "completed":
      return "ok";
    case "running":
    case "queued":
      return "warn";
    case "failed":
      return "error";
    default:
      return undefined;
  }
}

function fmtDt(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function ScanDetailPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const scan = useScanDetail(scanId ?? null);

  if (scan.isLoading) {
    return (
      <div className="p-6">
        <LoadingState label="Loading scan…" />
      </div>
    );
  }
  if (scan.isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="Failed to load scan"
          description={(scan.error as Error)?.message}
        />
      </div>
    );
  }
  if (!scan.data) {
    return (
      <div className="p-6">
        <EmptyState
          icon="info"
          title="Scan not found"
          description={`No scan with id ${scanId}.`}
        />
      </div>
    );
  }

  const s = scan.data;
  return (
    <>
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            <Icon name="refresh" size={16} />
            Scan detail
          </span>
        }
        sub={
          <span className="font-mono text-[12px] text-muted-2">{s.id}</span>
        }
        actions={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigate(-1)}
            title="Back"
          >
            <Icon name="arrow_left" size={12} />
            <span className="ml-1">Back</span>
          </Button>
        }
      />
      <div className="p-6 flex flex-col gap-4">
        <Card>
          <CardBody>
            <div className="flex items-center gap-3 flex-wrap">
              <Pill sev={statusSev(s.status)}>{s.status}</Pill>
              <Pill>{s.mode}</Pill>
              <span className="text-[12px] text-muted-2">
                Started {fmtDt(s.started_at)} · Finished{" "}
                {fmtDt(s.finished_at)}
              </span>
            </div>
            {/* Counter grid */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-4">
              <Counter label="Seen" value={s.files_seen} />
              <Counter label="Added" value={s.files_added} />
              <Counter label="Updated" value={s.files_updated} />
              <Counter label="Orphaned" value={s.files_orphaned} />
              <Counter label="Probe failures" value={s.probe_failures} />
            </div>
          </CardBody>
        </Card>

        {/* Stage 14 (audit follow-up): failed scans surface their
            error blob prominently. ``<pre>`` so newlines and shell
            output render verbatim. */}
        {s.status === "failed" && s.error ? (
          <Card>
            <CardBody>
              <div className="text-[11px] uppercase tracking-[0.06em] font-semibold text-muted-2 mb-2">
                Error
              </div>
              <pre
                data-testid="scan-error-block"
                className="whitespace-pre-wrap break-words text-[12px] font-mono text-sev-error bg-surface-sunk p-3 rounded-md m-0"
              >
                {s.error}
              </pre>
            </CardBody>
          </Card>
        ) : null}

        <Card>
          <CardBody>
            <div className="text-[12.5px]">
              <Link
                to={`/files?library_id=${encodeURIComponent(s.library_id)}`}
                className="text-muted hover:text-text inline-flex items-center gap-1.5"
              >
                Files in this library
                <Icon name="arrow_up_right" size={12} />
              </Link>
            </div>
          </CardBody>
        </Card>
      </div>
    </>
  );
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col">
      <div className="text-[10.5px] uppercase tracking-[0.06em] font-semibold text-muted-2">
        {label}
      </div>
      <div className="font-mono text-[18px] tabular-nums mt-0.5">{value}</div>
    </div>
  );
}
