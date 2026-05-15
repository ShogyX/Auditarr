/**
 * Stage 14 (audit follow-up) — manual housekeeping trigger +
 * last-run summary.
 *
 * Sits at the top of the Settings → System → Housekeeping sub-tab,
 * above the retention RuntimeSettingsPanel. Two affordances:
 *
 *   - "Run now" button — POSTs to ``/system/housekeeping/run``
 *     synchronously (per the Stage 14 guard rail, the trim runs on
 *     the API process, not the worker — the point is "I want it
 *     gone right now"). Toast confirms the row counts.
 *   - "Last run: <ts> — deleted N rows" line, pulled from the new
 *     ``housekeeping_runs`` table. Distinguishes ``manual`` vs
 *     ``scheduled`` runs via a small pill. Shows the error message
 *     when the last run failed.
 *
 * Both are gated on admin (the endpoints 403 for non-admins; the
 * card just hides itself rather than render a 403-y empty state).
 */

import { Card, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import {
  useHousekeepingLastRun,
  useRunHousekeeping,
} from "@/hooks/useSystem";
import { toast } from "@/lib/toast";
import { useAuthStore } from "@/stores/authStore";

function fmtDt(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function HousekeepingActionsCard() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  const lastRun = useHousekeepingLastRun();
  const runHousekeeping = useRunHousekeeping();

  if (!isAdmin) return null;

  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="text-[13px] font-semibold">Housekeeping</div>
            <div className="text-[11.5px] text-muted-2">
              Trim audit-style tables now or check when the cron last
              ran.
            </div>
          </div>
          <Button
            size="sm"
            variant="primary"
            disabled={runHousekeeping.isPending}
            onClick={() => {
              runHousekeeping.mutate(undefined, {
                onSuccess: (report) => {
                  toast(
                    `Housekeeping complete — deleted ${report.total} row${report.total === 1 ? "" : "s"}`,
                    "ok",
                  );
                },
                onError: (err) => {
                  toast(
                    `Housekeeping failed: ${(err as Error).message}`,
                    "error",
                  );
                },
              });
            }}
            title="Run housekeeping immediately"
          >
            <Icon name="refresh" size={12} />
            <span className="ml-1">
              {runHousekeeping.isPending ? "Running…" : "Run now"}
            </span>
          </Button>
        </div>

        {/* Last-run summary */}
        <div
          className="mt-3 text-[12px] text-muted-2"
          data-testid="housekeeping-last-run"
        >
          {lastRun.isLoading ? (
            <span>Loading last run…</span>
          ) : !lastRun.data ? (
            <span>Never run yet.</span>
          ) : (
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 flex-wrap">
                <Pill>{lastRun.data.trigger}</Pill>
                <span>
                  Last ran {fmtDt(lastRun.data.finished_at ?? lastRun.data.started_at)}
                </span>
                <span className="font-mono text-[11.5px]">
                  deliveries={lastRun.data.deliveries_deleted} ·
                  checks={lastRun.data.update_checks_deleted} ·
                  evals={lastRun.data.rule_evaluations_deleted} ·
                  jobs={lastRun.data.job_runs_deleted}
                </span>
              </div>
              {lastRun.data.error ? (
                <pre
                  className="whitespace-pre-wrap break-words text-[11.5px] font-mono text-sev-error bg-surface-sunk p-2 rounded-md m-0"
                  data-testid="housekeeping-last-error"
                >
                  {lastRun.data.error}
                </pre>
              ) : null}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
