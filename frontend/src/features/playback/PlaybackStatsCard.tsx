/**
 * Playback insights card (Stage 12 audit follow-up).
 *
 * Dashboard card with three tabs:
 *   - Top transcoded files (table)
 *   - Device matrix (per-(device_kind, decision) cell grid)
 *   - Decision trend (stacked daily bars)
 *
 * Honours the existing ``dashboardHidden`` collapse state with
 * key ``playback`` so the chevron in the card header collapses it
 * like every other dashboard card.
 *
 * Empty state surfaces when ALL three queries return zero rows —
 * a fresh install or one with no Plex/Jellyfin integrations yet.
 * Individual tab emptiness still renders the tab strip so the
 * operator can compare across tabs.
 */

import { useMemo, useState, type ReactNode } from "react";

import { Card, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import {
  useDecisionTrend,
  useDeviceMatrix,
  useTopTranscoded,
  type DeviceMatrixCell,
  type DecisionDayPoint,
  type TopTranscodedFile,
} from "@/hooks/usePlayback";
import { useUiStore } from "@/stores/uiStore";

type PlaybackTab = "transcoded" | "devices" | "trend";

export function PlaybackStatsCard() {
  const hidden = useUiStore((s) => s.dashboardHidden.includes("playback"));
  const toggle = useUiStore((s) => s.toggleDashboardSection);

  // Tab state. Default to the top-transcoded files — the highest-
  // signal panel for most operators ("which file keeps transcoding?").
  const [tab, setTab] = useState<PlaybackTab>("transcoded");

  const transcoded = useTopTranscoded({ days: 30, limit: 20 });
  const matrix = useDeviceMatrix({ days: 30 });
  const trend = useDecisionTrend({ days: 30 });

  // Global emptiness: all three queries returned zero rows. We
  // still render the card frame + tabs when at least one tab has
  // data — the empty state only fires when there's literally no
  // playback to discuss.
  const isLoading =
    transcoded.isLoading || matrix.isLoading || trend.isLoading;
  const isError =
    transcoded.isError || matrix.isError || trend.isError;
  const allEmpty =
    (transcoded.data?.items.length ?? 0) === 0 &&
    (matrix.data?.cells.length ?? 0) === 0 &&
    (trend.data?.points.length ?? 0) === 0;

  return (
    <Card>
      <CardHead
        title="Playback insights"
        subtitle="Plex / Jellyfin playback over the last 30 days"
        actions={
          <button
            type="button"
            onClick={() => toggle("playback")}
            className="shrink-0 text-muted-2 hover:text-text"
            aria-label={
              hidden ? "Expand Playback insights" : "Collapse Playback insights"
            }
            aria-expanded={!hidden}
            title={hidden ? "Expand" : "Collapse"}
          >
            <Icon name={hidden ? "chev_right" : "chev_down"} size={14} />
          </button>
        }
      />
      {!hidden ? (
        <div className="p-4 flex flex-col gap-3">
          {isLoading && !transcoded.data ? (
            <LoadingState label="Loading playback insights…" />
          ) : isError ? (
            <ErrorState
              title="Failed to load playback insights"
              description={
                (transcoded.error || matrix.error || trend.error)?.toString()
              }
            />
          ) : allEmpty ? (
            <EmptyState
              icon="info"
              title="No playback yet"
              description="Connect a Plex or Jellyfin integration. The poller fills this card as events arrive."
            />
          ) : (
            <>
              {/* Tab strip */}
              <div
                role="tablist"
                aria-label="Playback insights tabs"
                className="flex items-center gap-1 border-b border-border"
              >
                <TabButton
                  active={tab === "transcoded"}
                  onClick={() => setTab("transcoded")}
                  count={transcoded.data?.items.length ?? 0}
                >
                  Top transcoded
                </TabButton>
                <TabButton
                  active={tab === "devices"}
                  onClick={() => setTab("devices")}
                  count={matrix.data?.cells.length ?? 0}
                >
                  Device matrix
                </TabButton>
                <TabButton
                  active={tab === "trend"}
                  onClick={() => setTab("trend")}
                  count={trend.data?.points.length ?? 0}
                >
                  Decision trend
                </TabButton>
              </div>

              {/* Active panel */}
              <div
                role="tabpanel"
                aria-labelledby={`playback-tab-${tab}`}
                className="flex flex-col gap-2"
              >
                {tab === "transcoded" ? (
                  <TopTranscodedPanel items={transcoded.data?.items ?? []} />
                ) : tab === "devices" ? (
                  <DeviceMatrixPanel cells={matrix.data?.cells ?? []} />
                ) : (
                  <DecisionTrendPanel points={trend.data?.points ?? []} />
                )}
              </div>
            </>
          )}
        </div>
      ) : null}
    </Card>
  );
}

// ── Tab button ─────────────────────────────────────────────────
function TabButton({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count: number;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "text-[12.5px] px-3 py-1.5 border-b-2 -mb-px transition-colors",
        active
          ? "border-accent text-text"
          : "border-transparent text-muted-2 hover:text-text",
      )}
    >
      {children}
      {count > 0 ? (
        <span className="ml-2 text-[11px] text-muted-2 tabular-nums">
          {count}
        </span>
      ) : null}
    </button>
  );
}

// ── Tab panels ─────────────────────────────────────────────────
function TopTranscodedPanel({ items }: { items: TopTranscodedFile[] }) {
  if (items.length === 0) {
    return (
      <div className="px-3 py-4 text-[12.5px] text-muted italic">
        No transcodes in the last 30 days.
      </div>
    );
  }
  // Find the largest count so we can scale the inline bars.
  const max = Math.max(...items.map((i) => i.transcode_count), 1);
  return (
    <ul
      className="m-0 p-0 list-none"
      data-testid="playback-top-transcoded-list"
    >
      {items.map((item) => (
        <li
          key={item.media_file_id ?? `unresolved-${item.path}`}
          className="grid grid-cols-[1fr_auto] gap-3 items-center py-1.5 border-b border-border last:border-b-0"
        >
          <div className="min-w-0">
            <div className="text-[12.5px] truncate">
              {item.media_file_id === null ? (
                <Pill>unresolved</Pill>
              ) : null}
              <span className="ml-1 font-mono text-[11.5px] text-muted-2">
                {item.filename ?? item.path}
              </span>
            </div>
            {item.source_codec || item.target_codec ? (
              <div className="text-[11px] text-muted-2 mt-0.5">
                {item.source_codec ?? "?"} → {item.target_codec ?? "?"}
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <div
              className="h-1.5 rounded-full bg-accent/30 relative"
              style={{ width: 80 }}
              aria-hidden="true"
            >
              <div
                className="h-full bg-accent rounded-full"
                style={{
                  width: `${Math.max(4, (item.transcode_count / max) * 100)}%`,
                }}
              />
            </div>
            <span className="text-[12px] tabular-nums w-8 text-right">
              {fmtNum(item.transcode_count)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function DeviceMatrixPanel({ cells }: { cells: DeviceMatrixCell[] }) {
  // Pivot the flat cells into a row-per-device, column-per-decision
  // table. ``devices`` and ``decisions`` are the unique sorted axes.
  const { devices, decisions, cellMap, maxCount } = useMemo(() => {
    const devs = new Set<string>();
    const decs = new Set<string>();
    const map = new Map<string, number>();
    let max = 0;
    for (const c of cells) {
      devs.add(c.device_kind);
      decs.add(c.decision);
      map.set(`${c.device_kind}\u0001${c.decision}`, c.count);
      if (c.count > max) max = c.count;
    }
    return {
      devices: Array.from(devs).sort(),
      decisions: Array.from(decs).sort(),
      cellMap: map,
      maxCount: max || 1,
    };
  }, [cells]);

  if (cells.length === 0) {
    return (
      <div className="px-3 py-4 text-[12.5px] text-muted italic">
        No playback events recorded yet for the matrix.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto" data-testid="playback-device-matrix">
      <table className="min-w-full text-[12.5px] border-collapse">
        <thead>
          <tr className="text-muted-2 text-left">
            <th className="px-2 py-1 font-medium">Device</th>
            {decisions.map((d) => (
              <th key={d} className="px-2 py-1 font-medium">
                {d}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {devices.map((dev) => (
            <tr key={dev} className="border-t border-border">
              <td className="px-2 py-1.5 font-mono text-muted-2">{dev}</td>
              {decisions.map((dec) => {
                const count = cellMap.get(`${dev}\u0001${dec}`) ?? 0;
                const intensity = count === 0 ? 0 : count / maxCount;
                return (
                  <td
                    key={dec}
                    className="px-2 py-1.5 tabular-nums"
                    style={{
                      backgroundColor:
                        intensity > 0
                          ? `rgba(99, 102, 241, ${0.1 + intensity * 0.35})`
                          : undefined,
                    }}
                    data-cell={`${dev}:${dec}`}
                    data-count={count}
                  >
                    {count === 0 ? (
                      <span className="text-muted-2">–</span>
                    ) : (
                      count
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DecisionTrendPanel({ points }: { points: DecisionDayPoint[] }) {
  // Aggregate points by day, summing per-decision counts. The
  // backend already returns one row per (day, decision); we just
  // pivot into a per-day struct for the stacked-bar render.
  const { days, byDay, decisions, maxTotal } = useMemo(() => {
    const dayMap = new Map<string, Record<string, number>>();
    const decSet = new Set<string>();
    for (const p of points) {
      if (!dayMap.has(p.day)) dayMap.set(p.day, {});
      const row = dayMap.get(p.day)!;
      row[p.decision] = (row[p.decision] ?? 0) + p.count;
      decSet.add(p.decision);
    }
    const sortedDays = Array.from(dayMap.keys()).sort();
    let max = 0;
    for (const d of sortedDays) {
      const total = Object.values(dayMap.get(d)!).reduce(
        (s, n) => s + n,
        0,
      );
      if (total > max) max = total;
    }
    return {
      days: sortedDays,
      byDay: dayMap,
      decisions: Array.from(decSet).sort(),
      maxTotal: max || 1,
    };
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="px-3 py-4 text-[12.5px] text-muted italic">
        No daily trend data yet.
      </div>
    );
  }

  // Color palette per decision. Stable order means a given decision
  // always gets the same color.
  const colors: Record<string, string> = {
    direct_play: "var(--sev-ok, #10b981)",
    direct_stream: "var(--sev-info, #38bdf8)",
    transcode: "var(--sev-warn, #f59e0b)",
    failed: "var(--sev-error, #ef4444)",
  };

  return (
    <div data-testid="playback-decision-trend">
      <div className="flex items-end gap-1 h-24 mt-2">
        {days.map((day) => {
          const row = byDay.get(day)!;
          const total = Object.values(row).reduce((s, n) => s + n, 0);
          const totalHeight = (total / maxTotal) * 100;
          return (
            <div
              key={day}
              className="flex flex-col-reverse flex-1 min-w-0"
              title={`${day}: ${total} events`}
              style={{ height: `${totalHeight}%` }}
              data-day={day}
            >
              {decisions.map((dec) => {
                const count = row[dec] ?? 0;
                if (count === 0) return null;
                const pct = (count / total) * 100;
                return (
                  <div
                    key={dec}
                    style={{
                      height: `${pct}%`,
                      backgroundColor: colors[dec] ?? "var(--muted)",
                    }}
                    title={`${dec}: ${count}`}
                  />
                );
              })}
            </div>
          );
        })}
      </div>
      {/* Legend */}
      <div className="flex items-center gap-3 mt-3 text-[11px] text-muted-2 flex-wrap">
        {decisions.map((dec) => (
          <span key={dec} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm"
              style={{ backgroundColor: colors[dec] ?? "var(--muted)" }}
            />
            {dec}
          </span>
        ))}
        <span className="ml-auto">{days.length} days</span>
      </div>
    </div>
  );
}
