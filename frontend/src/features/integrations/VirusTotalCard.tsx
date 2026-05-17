/**
 * Stage 10 (v1.7) — VirusTotal card on the Integrations page.
 *
 * Per plan §520: shows quota used / limit / queue size. Per
 * addendum B.7: surfaces all three quota windows (per-minute /
 * per-day / per-month) so operators on the free tier can see
 * which limit they're closest to.
 *
 * Empty state (no VT integration configured) renders a brief
 * "Add VirusTotal integration" call-to-action rather than the
 * usual quota bars — there's nothing to report yet.
 *
 * Polls every 30s via :func:`useVirustotalStatus`.
 */

import { Card, CardBody, CardHead } from "@/components/ui/Card";
import { Pill } from "@/components/ui/Pill";
import { ErrorState, LoadingState } from "@/components/ui/States";
import { useVirustotalStatus } from "@/hooks/useIntegrations";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";

interface QuotaRowProps {
  label: string;
  used: number;
  cap: number;
  /** Brief explanation surfaced under the label. */
  help?: string;
}

function QuotaRow({ label, used, cap, help }: QuotaRowProps) {
  const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  // Colour the bar by saturation — operators glance at it and
  // know which window is most stressed.
  const sev = pct >= 90 ? "high" : pct >= 60 ? "warn" : "ok";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <div className="text-[12px] font-medium text-text">{label}</div>
          {help ? (
            <div className="text-[11px] text-muted-2">{help}</div>
          ) : null}
        </div>
        <div className="text-[12px] tabular-nums text-muted">
          {fmtNum(used)} / {fmtNum(cap)}
        </div>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded bg-surface-sunk">
        <div
          className={cn(
            "h-full transition-all",
            sev === "high" && "bg-red-500",
            sev === "warn" && "bg-amber-500",
            sev === "ok" && "bg-accent",
          )}
          style={{ width: `${pct}%` }}
          role="progressbar"
          aria-valuenow={used}
          aria-valuemin={0}
          aria-valuemax={cap}
          aria-label={`${label} quota usage`}
        />
      </div>
    </div>
  );
}

export function VirusTotalCard() {
  const status = useVirustotalStatus();

  if (status.isLoading) {
    return (
      <Card>
        <CardHead title="VirusTotal" subtitle="Quota and queue" />
        <CardBody>
          <LoadingState label="Loading VirusTotal status…" />
        </CardBody>
      </Card>
    );
  }

  if (status.isError) {
    return (
      <Card>
        <CardHead title="VirusTotal" subtitle="Quota and queue" />
        <CardBody>
          <ErrorState
            title="Couldn't load VirusTotal status"
            description={(status.error as Error)?.message}
          />
        </CardBody>
      </Card>
    );
  }

  const data = status.data;
  if (!data) return null;

  // Empty state — no integration configured.
  if (!data.configured) {
    return (
      <Card>
        <CardHead title="VirusTotal" subtitle="Hash-based file reputation" />
        <CardBody>
          <div className="py-2 text-[12px] text-muted">
            No VirusTotal integration configured. Add one via the
            connector directory above to start checking file hashes
            for known malicious or suspicious content.
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card data-testid="virustotal-card">
      <CardHead
        title="VirusTotal"
        subtitle={
          data.enabled ? "Active — polling quota" : "Configured but disabled"
        }
        actions={
          <Pill sev={data.enabled ? "ok" : "warn"}>
            {data.enabled ? "enabled" : "disabled"}
          </Pill>
        }
      />
      <CardBody>
        <div className="flex flex-col gap-4">
          {/* Three quota windows side by side on wide screens,
              stacked on narrow ones. */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <QuotaRow
              label="Per-minute"
              used={data.minute_used}
              cap={data.minute_cap}
              help="VT's physical free-tier ceiling"
            />
            <QuotaRow
              label="Per-day"
              used={data.day_used}
              cap={data.day_cap}
              help="Resets at UTC midnight"
            />
            <QuotaRow
              label="Per-month"
              used={data.month_used}
              cap={data.month_cap}
              help="Resets on the 1st of each UTC month"
            />
          </div>

          {/* Queue size + last check timestamp on a single row. */}
          <div className="flex items-baseline justify-between border-t border-border pt-3">
            <div>
              <div className="text-[12px] font-medium text-text">
                Files awaiting lookup
              </div>
              <div className="text-[11px] text-muted-2">
                Scanned files with a hash, no VT result yet
              </div>
            </div>
            <div
              className="text-[18px] font-semibold tabular-nums text-text"
              data-testid="virustotal-queue-size"
            >
              {fmtNum(data.queue_size)}
            </div>
          </div>

          {data.last_check_at ? (
            <div className="text-[11px] text-muted-2">
              Last lookup: {new Date(data.last_check_at).toLocaleString()}
            </div>
          ) : (
            <div className="text-[11px] text-muted-2">
              No lookups yet this session.
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
