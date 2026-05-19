/**
 * Settings → System → Tags. Admin-only management surface for the
 * tag catalog.
 *
 * Two operator workflows:
 *   1. "I imported a bunch of useless tags from Sonarr; drop them all."
 *      → filter the table by source, hit "Delete by source".
 *   2. "I never want this particular tag mirrored on any file."
 *      → click the row's trash icon to delete by (name, source), or
 *      switch the scope to ``name`` and remove across all sources.
 *
 * Re-imports happen on the next integration sync; the inline note
 * points the operator at the per-integration ``tag_denylist`` for
 * permanent suppression.
 */

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  useBulkDeleteTags,
  useTagSummary,
  type TagSummaryRow,
} from "@/hooks/useTags";
import { fmtNum } from "@/lib/format";
import { toast } from "@/lib/toast";
import { useAuthStore } from "@/stores/authStore";

export function TagsPanel() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  const summary = useTagSummary();
  const del = useBulkDeleteTags();

  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("");

  const sources = useMemo(() => {
    const set = new Set<string>();
    for (const row of summary.data ?? []) set.add(row.source);
    return Array.from(set).sort();
  }, [summary.data]);

  const filtered = useMemo(() => {
    const rows = summary.data ?? [];
    const s = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (sourceFilter && row.source !== sourceFilter) return false;
      if (s && !row.name.toLowerCase().includes(s)) return false;
      return true;
    });
  }, [summary.data, search, sourceFilter]);

  const runDelete = async (
    filter: { name?: string; source?: string },
    confirmText: string,
  ) => {
    if (!window.confirm(confirmText)) return;
    try {
      const r = await del.mutateAsync(filter);
      toast(
        `Removed ${fmtNum(r.deleted)} tag${r.deleted === 1 ? "" : "s"}`,
        "ok",
      );
    } catch (e) {
      toast(`Delete failed: ${(e as Error)?.message ?? "Unknown error"}`, "error");
    }
  };

  if (summary.isLoading) {
    return (
      <Card>
        <CardBody>
          <LoadingState label="Loading tag catalog…" />
        </CardBody>
      </Card>
    );
  }
  if (summary.isError) {
    return (
      <Card>
        <CardBody>
          <ErrorState
            title="Failed to load tags"
            description={(summary.error as Error)?.message}
          />
        </CardBody>
      </Card>
    );
  }
  const rows = summary.data ?? [];

  return (
    <Card>
      <CardHead
        title="Tags"
        subtitle={
          rows.length === 0
            ? "No tags yet — integrations and rules populate this list."
            : `${fmtNum(rows.length)} distinct tag${rows.length === 1 ? "" : "s"} across ${sources.length} source${sources.length === 1 ? "" : "s"}`
        }
      />
      <CardBody>
        {rows.length === 0 ? (
          <EmptyState
            icon="folder"
            title="No tags imported yet"
            description="Tags appear here after the first integration sync or rule run."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {/* Filter row */}
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="search"
                placeholder="Search by name…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="input min-w-[180px]"
                aria-label="search tag names"
              />
              <select
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
                className="input"
                aria-label="filter by source"
              >
                <option value="">All sources</option>
                {sources.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              {sourceFilter && isAdmin ? (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={del.isPending}
                  onClick={() =>
                    runDelete(
                      { source: sourceFilter },
                      `Delete every tag whose source is "${sourceFilter}"? This affects all files carrying them and cannot be undone via the UI. Re-imports will repopulate on the next integration sync.`,
                    )
                  }
                  title={`Drop every tag synced from ${sourceFilter}`}
                >
                  <Icon name="trash" size={11} />
                  <span className="ml-1">Delete all from {sourceFilter}</span>
                </Button>
              ) : null}
              {filtered.length !== rows.length ? (
                <span className="text-[11.5px] text-muted-2">
                  Showing {filtered.length} of {rows.length}
                </span>
              ) : null}
            </div>

            {/* Table */}
            <div className="border border-border rounded-md overflow-hidden">
              <table className="w-full text-[12.5px]">
                <thead className="bg-surface-2 text-muted-2">
                  <tr>
                    <th className="text-left px-2.5 py-1.5 font-medium">Name</th>
                    <th className="text-left px-2.5 py-1.5 font-medium">Source</th>
                    <th className="text-right px-2.5 py-1.5 font-medium">Files</th>
                    {isAdmin ? (
                      <th className="text-right px-2.5 py-1.5 font-medium w-20">
                        Actions
                      </th>
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row) => (
                    <TagRow
                      key={`${row.source}__${row.name}`}
                      row={row}
                      isAdmin={isAdmin}
                      onDelete={(scope) => {
                        if (scope === "pair") {
                          return runDelete(
                            { name: row.name, source: row.source },
                            `Delete tag "${row.name}" (from ${row.source}) across all ${row.file_count} files?`,
                          );
                        }
                        return runDelete(
                          { name: row.name },
                          `Delete tag "${row.name}" from every file regardless of source?`,
                        );
                      }}
                      busy={del.isPending}
                    />
                  ))}
                  {filtered.length === 0 ? (
                    <tr>
                      <td
                        colSpan={isAdmin ? 4 : 3}
                        className="text-center text-muted-2 px-2.5 py-4"
                      >
                        No tags match the current filter.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <div className="text-[11.5px] text-muted-2">
              <Icon name="info" size={11} className="inline mr-1" />
              Deleting a tag drops it from every file that carries it.
              The next integration sync will re-import it unless you
              add the name to that integration's <code className="font-mono">tag_denylist</code>.
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function TagRow({
  row,
  isAdmin,
  onDelete,
  busy,
}: {
  row: TagSummaryRow;
  isAdmin: boolean;
  onDelete: (scope: "pair" | "name") => void;
  busy: boolean;
}) {
  return (
    <tr className="border-t border-border">
      <td className="px-2.5 py-1.5">
        <span className="font-mono">{row.name}</span>
      </td>
      <td className="px-2.5 py-1.5">
        <Pill className="text-muted-2 border-border bg-surface-2">
          {row.source}
        </Pill>
      </td>
      <td className="px-2.5 py-1.5 text-right tabular-nums">
        {fmtNum(row.file_count)}
      </td>
      {isAdmin ? (
        <td className="px-2.5 py-1.5 text-right">
          <button
            type="button"
            onClick={() => onDelete("pair")}
            disabled={busy}
            className="text-muted-2 hover:text-sev-error disabled:opacity-50"
            title={`Delete "${row.name}" from ${row.source} (this row only)`}
            aria-label={`delete ${row.name} from ${row.source}`}
          >
            <Icon name="trash" size={12} />
          </button>
        </td>
      ) : null}
    </tr>
  );
}
