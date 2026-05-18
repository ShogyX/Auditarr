/**
 * v1.9 Stage 8.1 — Logs page.
 *
 * Surfaces the backend's in-memory log ring buffer as a
 * filterable + auto-tail-able table. Operator picks service /
 * level filters, optionally turns on tail mode, and optionally
 * downloads an NDJSON export of the current filter slice.
 *
 * Admin-only at the API layer; this page doesn't itself gate
 * access — a non-admin sees the React Query error state for
 * the 403.
 */

import { useMemo, useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import {
  downloadLogsNdjson,
  useLogs,
  type LogRecord,
} from "@/hooks/useLogs";

const SERVICE_OPTIONS = [
  { value: "all", label: "All" },
  { value: "api", label: "API" },
  { value: "worker", label: "Worker" },
  { value: "scheduler", label: "Scheduler" },
  { value: "playback", label: "Playback" },
  { value: "integrations", label: "Integrations" },
  { value: "rules", label: "Rules" },
  { value: "database", label: "Database" },
  { value: "events", label: "Events" },
];

const LEVEL_OPTIONS = [
  { value: "", label: "All levels" },
  { value: "info", label: "Info+" },
  { value: "warning", label: "Warning+" },
  { value: "error", label: "Error+" },
];

export function LogsPage() {
  const [service, setService] = useState("all");
  const [level, setLevel] = useState("");
  const [tail, setTail] = useState(false);
  const [search, setSearch] = useState("");

  const query = useLogs({ service, level, tail, limit: 500 });

  const filtered = useMemo<LogRecord[]>(() => {
    const all: LogRecord[] = query.data?.records ?? [];
    if (!search.trim()) return all;
    const needle = search.toLowerCase();
    return all.filter((r: LogRecord) => {
      if (r.event.toLowerCase().includes(needle)) return true;
      if (r.logger.toLowerCase().includes(needle)) return true;
      for (const value of Object.values(r.context ?? {})) {
        if (String(value).toLowerCase().includes(needle)) return true;
      }
      return false;
    });
  }, [query.data?.records, search]);

  const [exportError, setExportError] = useState<string | null>(null);

  // v1.9.1 — distinguish "buffer is empty" from "your filter
  // matches nothing." The two empty-state messages diverge below.
  const hasActiveFilter =
    service !== "all" || level !== "" || search.trim() !== "";

  async function onExport() {
    setExportError(null);
    try {
      await downloadLogsNdjson({ service, level });
    } catch (err) {
      setExportError(
        err instanceof Error ? err.message : "Export failed",
      );
    }
  }

  return (
    <>
      <PageHeader
        title="Logs"
        sub="In-memory ring buffer of recent log records (admin only)."
      />
      {/* v1.9.1 — the page previously had no padding wrapper and
          bled to the viewport edge, including under the side nav
          on wide screens. Wrap in the same p-4 / xl:p-6 padding
          pattern the Rules / Files pages use so the content
          stays clear of the sidebar without leaving an awkward
          desert of margin. */}
      <div
        className="p-4 xl:p-6 space-y-3"
        data-testid="logs-page"
      >

      {/* Filter bar. */}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-0.5 text-[11.5px]">
          Service
          <select
            value={service}
            onChange={(e) => setService(e.target.value)}
            className="rounded border border-border bg-surface px-2 py-1 text-[12.5px]"
            aria-label="service filter"
          >
            {SERVICE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-0.5 text-[11.5px]">
          Level
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="rounded border border-border bg-surface px-2 py-1 text-[12.5px]"
            aria-label="level filter"
          >
            {LEVEL_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-0.5 text-[11.5px] flex-1 min-w-[180px]">
          Search
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter event / logger / context…"
            className="rounded border border-border bg-surface px-2 py-1 text-[12.5px]"
            aria-label="search filter"
          />
        </label>

        <label className="inline-flex items-center gap-1.5 text-[12.5px]">
          <input
            type="checkbox"
            checked={tail}
            onChange={(e) => setTail(e.target.checked)}
            aria-label="auto-tail"
          />
          Auto-tail
        </label>

        <Button size="sm" variant="ghost" onClick={onExport}>
          <Icon name="download" size={12} />
          <span className="ml-1">Export NDJSON</span>
        </Button>
      </div>

      {exportError ? (
        <div
          className="text-[11.5px] text-sev-error"
          data-testid="logs-export-error"
        >
          {exportError}
        </div>
      ) : null}

      {/* Buffer status. */}
      {query.data ? (
        <div className="text-[11.5px] text-muted-2 flex items-center gap-3">
          <span>
            {query.data.count} of {query.data.total_buffered} buffered
            (capacity {query.data.buffer_capacity})
          </span>
          {query.data.last_error_at ? (
            <span data-testid="recent-error-indicator">
              <Pill sev="error">Recent error</Pill>
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Table. */}
      <div className="rounded border border-border overflow-hidden">
        <table
          className="w-full text-[12.5px] font-mono"
          data-testid="logs-table"
        >
          <thead className="bg-surface-2 text-[11.5px]">
            <tr>
              <th className="px-2 py-1 text-left">Time</th>
              <th className="px-2 py-1 text-left">Level</th>
              <th className="px-2 py-1 text-left">Service</th>
              <th className="px-2 py-1 text-left">Event</th>
              <th className="px-2 py-1 text-left">Context</th>
            </tr>
          </thead>
          <tbody>
            {query.isLoading ? (
              <tr>
                <td className="px-2 py-2 text-muted-2" colSpan={5}>
                  Loading…
                </td>
              </tr>
            ) : query.isError ? (
              <tr>
                <td className="px-2 py-2 text-sev-error" colSpan={5}>
                  {(query.error as Error)?.message ?? "Failed to load logs"}
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td className="px-2 py-2 text-muted-2" colSpan={5}>
                  {/* v1.9.1 — operator-friendly empty state.
                      An empty buffer (fresh restart, quiet
                      system) reads VERY differently from "your
                      filter has no matches" — they need
                      different responses. */}
                  {query.data && query.data.total_buffered === 0 ? (
                    <span>
                      No records in the buffer yet. The buffer
                      fills as the backend logs events — errors,
                      warnings, integration calls, rule
                      evaluations. A freshly-restarted process
                      starts empty; activity will populate this
                      page within seconds.
                    </span>
                  ) : hasActiveFilter ? (
                    <span>
                      No records match the current filter.{" "}
                      <button
                        type="button"
                        className="underline text-accent"
                        onClick={() => {
                          setService("all");
                          setLevel("");
                          setSearch("");
                        }}
                      >
                        Clear filters
                      </button>{" "}
                      to see all{" "}
                      {query.data?.total_buffered ?? 0} buffered records.
                    </span>
                  ) : (
                    <span>No records match the current filter.</span>
                  )}
                </td>
              </tr>
            ) : (
              filtered.map((r, i) => (
                <tr
                  key={i}
                  className="border-t border-border align-top"
                  data-testid="logs-row"
                >
                  <td className="px-2 py-1 whitespace-nowrap text-muted-2">
                    {fmtTime(r.timestamp)}
                  </td>
                  <td className="px-2 py-1">
                    <LevelPill level={r.level} />
                  </td>
                  <td className="px-2 py-1 text-muted-2">
                    {r.category ?? "—"}
                  </td>
                  <td className="px-2 py-1">{r.event}</td>
                  <td className="px-2 py-1 text-muted-2 break-all">
                    {fmtContext(r.context)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      </div>
    </>
  );
}

function LevelPill({ level }: { level: LogRecord["level"] }) {
  const sev =
    level === "error" || level === "critical"
      ? "error"
      : level === "warning"
        ? "warn"
        : "ok";
  return <Pill sev={sev}>{level}</Pill>;
}

function fmtTime(ts: string): string {
  // Strip microseconds + tz suffix for display compactness:
  // "2026-05-18T07:00:01.123456+00:00" → "07:00:01"
  const match = ts.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1]! : ts;
}

function fmtContext(
  context: Record<string, string | number | boolean>,
): string {
  const entries = Object.entries(context ?? {});
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(" ");
}
