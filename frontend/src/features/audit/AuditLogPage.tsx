/**
 * Stage 14 (audit follow-up) — Audit log viewer.
 *
 * Admin-only page at ``/settings/audit``. Backed by the existing
 * ``GET /audit/log`` endpoint (Stage 14 extended its filter set
 * to support date-range + cursor pagination).
 *
 * Filters:
 *   - actor_id (free-text — autocomplete from the users list is a
 *     future polish)
 *   - action (free-text, exact match)
 *   - since / until (datetime-local inputs)
 *
 * Pagination is cursor-style ("Load more"): each "Load more" click
 * passes the last row's id as ``before_id`` to fetch the next
 * batch. Per the plan's guard rail, the audit log's append-only
 * shape makes offset pagination unstable under insert load — the
 * monotonically-growing id is the stable cursor.
 *
 * Per the guard rail, ``limit`` is capped at 500 server-side; the
 * UI uses 100 per page so a typical "show me yesterday's activity"
 * request stays responsive.
 */

import { useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Pill, Tag } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { useAuditLog, type AuditLogEntry } from "@/hooks/useSystem";

const PAGE_SIZE = 100;

/** Convert a datetime-local input value ("2026-05-14T08:00") to a
 *  full ISO string with timezone. The browser supplies local-time
 *  values without a zone marker; we serialize via the Date
 *  constructor so the server sees the correct UTC instant. */
function localToIso(value: string): string | undefined {
  if (!value) return undefined;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString();
}

export function AuditLogPage() {
  const [actorId, setActorId] = useState("");
  const [action, setAction] = useState("");
  const [sinceLocal, setSinceLocal] = useState("");
  const [untilLocal, setUntilLocal] = useState("");

  // Per-page result buffer. New filter values reset the buffer to
  // the first page; "Load more" pushes the next page in.
  const [pages, setPages] = useState<AuditLogEntry[][]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  // Used to reset the buffer when the filters change.
  const filterKey = useMemo(
    () => JSON.stringify({ actorId, action, sinceLocal, untilLocal }),
    [actorId, action, sinceLocal, untilLocal],
  );
  const [activeFilterKey, setActiveFilterKey] = useState(filterKey);

  const filters = useMemo(
    () => ({
      actor_id: actorId.trim() || undefined,
      action: action.trim() || undefined,
      since: localToIso(sinceLocal),
      until: localToIso(untilLocal),
      before_id: cursor ?? undefined,
      limit: PAGE_SIZE,
    }),
    [actorId, action, sinceLocal, untilLocal, cursor],
  );

  const query = useAuditLog(filters);

  // When the filter inputs change, drop the buffer + cursor and
  // let the next render's query refetch as a fresh page.
  useEffect(() => {
    if (filterKey !== activeFilterKey) {
      setActiveFilterKey(filterKey);
      setPages([]);
      setCursor(null);
    }
  }, [filterKey, activeFilterKey]);

  // Push every successful page into the buffer. The query key
  // changes when ``before_id`` advances, so each fetch is a fresh
  // result; we append it without de-duping (the cursor guarantees
  // disjointness on the server side too).
  useEffect(() => {
    if (!query.data) return;
    setPages((prev) => {
      // Skip if the last page already matches (avoids double-push
      // from React 18 strict-mode double-effect).
      const last = prev.at(-1);
      if (last && last === query.data) return prev;
      if (last && last.length > 0 && query.data.length > 0) {
        if (last[0]!.id === query.data[0]!.id) return prev;
      }
      return [...prev, query.data];
    });
  }, [query.data]);

  const flat = pages.flat();
  const lastId = flat.length > 0 ? flat[flat.length - 1]!.id : null;
  // "Load more" is only useful when the last fetch filled a page
  // (suggesting more rows exist). On a partial page we hide it.
  const lastBatch = pages.at(-1) ?? [];
  const canLoadMore = lastBatch.length === PAGE_SIZE;

  return (
    <>
      <PageHeader
        title="Audit log"
        sub="System events. Read-only."
        helpKey="settings.audit"
      />
      <div className="p-6 flex flex-col gap-4">
        <Card>
          <CardHead title="Filters" />
          <CardBody>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <Field label="Actor id">
                <Input
                  value={actorId}
                  onChange={(e) => setActorId(e.target.value)}
                  placeholder="UUID"
                />
              </Field>
              <Field label="Action">
                <Input
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                  placeholder="e.g. auth.login"
                />
              </Field>
              <Field label="Since">
                <Input
                  type="datetime-local"
                  value={sinceLocal}
                  onChange={(e) => setSinceLocal(e.target.value)}
                />
              </Field>
              <Field label="Until">
                <Input
                  type="datetime-local"
                  value={untilLocal}
                  onChange={(e) => setUntilLocal(e.target.value)}
                />
              </Field>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHead
            title="Events"
            subtitle={`${flat.length} loaded`}
            actions={
              canLoadMore && lastId != null ? (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={query.isFetching}
                  onClick={() => {
                    setCursor(lastId);
                  }}
                  title="Load older entries"
                >
                  <Icon name="chev_down" size={12} />
                  <span className="ml-1">
                    {query.isFetching ? "Loading…" : "Load more"}
                  </span>
                </Button>
              ) : null
            }
          />
          <CardBodyFlush>
            {query.isPending && !query.data ? (
              <div className="px-4 py-6">
                <LoadingState label="Loading audit log…" />
              </div>
            ) : query.isError ? (
              <div className="px-4 py-6">
                <ErrorState
                  title="Failed to load audit log"
                  description={(query.error as Error)?.message}
                />
              </div>
            ) : flat.length === 0 ? (
              <div className="px-4 py-6">
                <EmptyState
                  icon="info"
                  title="No entries"
                  description="The audit log is empty or no rows match your filters."
                />
              </div>
            ) : (
              <div className="files-table-wrap">
                <table
                  className="files-table"
                  role="grid"
                  data-testid="audit-log-table"
                >
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Actor</th>
                      <th>Action</th>
                      <th>Target</th>
                      <th>IP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {flat.map((row) => (
                      <tr key={row.id} className="files-table-row">
                        <td className="text-[11.5px] text-muted-2 font-mono">
                          {new Date(row.occurred_at).toLocaleString()}
                        </td>
                        <td className="text-[12px]">
                          {row.actor_label ?? (
                            <span className="text-muted-2">system</span>
                          )}
                        </td>
                        <td>
                          <Tag>{row.action}</Tag>
                        </td>
                        <td className="text-[11.5px] font-mono text-muted-2">
                          {row.target_type ? `${row.target_type}:` : ""}
                          {row.target_id ?? (
                            <span className="text-muted-2">—</span>
                          )}
                        </td>
                        <td className="text-[11.5px] font-mono text-muted-2">
                          {row.ip_address ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBodyFlush>
        </Card>

        <div className="text-[11.5px] text-muted-2 px-1">
          <Pill>500 row cap per request</Pill>
          <span className="ml-2">
            Pagination uses the row id as a cursor — stable across
            inserts.
          </span>
        </div>
      </div>
    </>
  );
}
