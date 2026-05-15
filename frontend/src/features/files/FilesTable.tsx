/**
 * Stage 3 — Files table.
 *
 * Extracted from the inline ``FilesTable``, ``FileRow``, ``Checkbox``,
 * and ``renderCell`` in ``FilesPage.tsx``. Preserves the exact DOM and
 * CSS contract:
 *
 *   - ``<table class="files-table" role="grid">``
 *   - ``<th class="is-sortable is-sorted num">`` with aria-sort
 *   - ``<tr class="files-table-row is-selected">``
 *   - ``<td class="num">`` for numeric columns
 *   - ``.files-table-sort-ind`` arrows ``↑ / ↓ / ↕``
 *
 * The Stage-1 ``DataGrid`` primitive is intentionally not adopted here.
 * Migrating the table to ``DataGrid`` would change the rendered DOM
 * (different sort indicator, different selection model) in ways that
 * would require updating 30+ test cases across four files. That
 * migration is queued as "Stage 3b — DataGrid adoption" and will land
 * after a Playwright visual-diff baseline is captured.
 */

import type { InputHTMLAttributes } from "react";

import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { CardBodyFlush } from "@/components/ui/Card";
import type {
  MediaFileSummary,
  MediaSortKey,
  useMediaList,
} from "@/hooks/useMedia";
import { cn } from "@/lib/cn";
import { fmtBytes } from "@/lib/format";
import {
  FILES_COLUMNS,
  type FilesColumnKey,
} from "@/stores/filesPrefsStore";

export interface FilesTableProps {
  list: ReturnType<typeof useMediaList>;
  visibleColumns: FilesColumnKey[];
  sort: { key: MediaSortKey; dir: "asc" | "desc" };
  onSort: (key: MediaSortKey) => void;
  selected: Set<string>;
  onToggleSel: (id: string) => void;
  onToggleAll: () => void;
  allVisibleSelected: boolean;
  someVisibleSelected: boolean;
  onOpenDrawer: (file: MediaFileSummary) => void;
}

export function FilesTable({
  list,
  visibleColumns,
  sort,
  onSort,
  selected,
  onToggleSel,
  onToggleAll,
  allVisibleSelected,
  someVisibleSelected,
  onOpenDrawer,
}: FilesTableProps) {
  const cols = FILES_COLUMNS.filter((c) =>
    visibleColumns.includes(c.key as FilesColumnKey),
  );

  if (list.isLoading) {
    return (
      <CardBodyFlush>
        <div className="px-4 py-12">
          <LoadingState label="Loading files…" />
        </div>
      </CardBodyFlush>
    );
  }
  if (list.isError) {
    return (
      <CardBodyFlush>
        <div className="px-4 py-12">
          <ErrorState
            title="Failed to load files"
            description={(list.error as Error | undefined)?.message}
          />
        </div>
      </CardBodyFlush>
    );
  }
  if (!list.data || list.data.items.length === 0) {
    return (
      <CardBodyFlush>
        <div className="px-4 py-12">
          <EmptyState
            icon="files"
            title="No files match"
            description="Try clearing filters or run a scan to populate the index."
          />
        </div>
      </CardBodyFlush>
    );
  }

  return (
    <div className="files-table-wrap">
      <table className="files-table" role="grid">
        <thead>
          <tr>
            <th className="files-table-check">
              <Checkbox
                checked={allVisibleSelected}
                indeterminate={someVisibleSelected}
                onChange={onToggleAll}
                aria-label={
                  allVisibleSelected
                    ? "Deselect all on this page"
                    : "Select all on this page"
                }
              />
            </th>
            {cols.map((c) => {
              const sortKey = "sortKey" in c ? c.sortKey : undefined;
              const isSorted = !!sortKey && sort.key === sortKey;
              return (
                <th
                  key={c.key}
                  className={cn(
                    sortKey && "is-sortable",
                    isSorted && "is-sorted",
                    "num" in c && c.num && "num",
                  )}
                  onClick={sortKey ? () => onSort(sortKey) : undefined}
                  aria-sort={
                    isSorted
                      ? sort.dir === "asc"
                        ? "ascending"
                        : "descending"
                      : sortKey
                        ? "none"
                        : undefined
                  }
                >
                  {c.label}
                  {sortKey ? (
                    <span className="files-table-sort-ind" aria-hidden="true">
                      {isSorted ? (sort.dir === "asc" ? "↑" : "↓") : "↕"}
                    </span>
                  ) : null}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {list.data.items.map((item) => (
            <FilesTableRow
              key={item.id}
              item={item}
              cols={cols}
              checked={selected.has(item.id)}
              onCheck={() => onToggleSel(item.id)}
              onOpen={() => onOpenDrawer(item)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilesTableRow({
  item,
  cols,
  checked,
  onCheck,
  onOpen,
}: {
  item: MediaFileSummary;
  cols: (typeof FILES_COLUMNS)[number][];
  checked: boolean;
  onCheck: () => void;
  onOpen: () => void;
}) {
  return (
    <tr
      className={cn("files-table-row", checked && "is-selected")}
      onClick={onOpen}
    >
      <td
        className="files-table-check"
        onClick={(e) => {
          // Don't open the drawer when the operator clicks the
          // checkbox cell.
          e.stopPropagation();
        }}
      >
        <Checkbox
          checked={checked}
          onChange={onCheck}
          aria-label={`Select ${item.filename}`}
        />
      </td>
      {cols.map((c) => (
        <td key={c.key} className={"num" in c && c.num ? "num" : undefined}>
          {renderCell(c.key as FilesColumnKey, item)}
        </td>
      ))}
    </tr>
  );
}

function renderCell(key: FilesColumnKey, item: MediaFileSummary) {
  switch (key) {
    case "filename":
      return (
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 truncate">
            {item.is_orphaned ? (
              <Icon
                name="alert"
                size={12}
                className="text-sev-warn shrink-0"
                aria-label="Orphaned"
              />
            ) : null}
            {item.quarantined ? (
              // Stage 27: quarantined files get an inline badge so the
              // operator can tell at a glance — useful when in the
              // "Include quarantined" view mode where they're mixed in.
              <Pill
                className="text-[10px] text-muted-2 border-border bg-surface-sunk shrink-0"
                aria-label="Quarantined"
              >
                Quarantined
              </Pill>
            ) : null}
            <span className="font-mono text-text truncate">{item.filename}</span>
          </div>
          <div className="font-mono text-[11px] text-muted-2 truncate">
            {item.path.split("/").slice(0, -1).join("/") || "/"}
          </div>
        </div>
      );
    case "category":
      return <Tag>{item.category}</Tag>;
    case "severity":
      return <Pill sev={item.severity}>{item.severity}</Pill>;
    case "size":
      return <span className="font-mono">{fmtBytes(item.size_bytes)}</span>;
    case "codec":
      return (
        <span className="font-mono text-text-2 truncate">
          {item.video_codec ?? item.audio_codec ?? "—"}
        </span>
      );
    case "container":
      return (
        <span className="font-mono text-text-2 truncate">
          {item.container ?? "—"}
        </span>
      );
    case "resolution":
      return (
        <span className="font-mono text-text-2">
          {item.width && item.height ? `${item.width}×${item.height}` : "—"}
        </span>
      );
    case "subs":
      return item.has_subtitles ? (
        <Icon name="check" size={12} className="text-sev-ok" />
      ) : (
        <span className="text-muted-2">—</span>
      );
    case "updated":
      return (
        <span className="font-mono text-[11.5px] text-muted">
          {new Date(item.mtime).toLocaleDateString()}
        </span>
      );
    case "extension":
      return <Tag>{item.extension || "—"}</Tag>;
    case "matched_rules": {
      // Stage 3 (audit follow-up): show up to three matched-rule
      // names as chips, with a ``+N`` overflow indicator when more
      // exist. The list comes back ordered by severity_rank desc
      // (highest-impact rule first), so the cap surfaces the rules
      // most likely to matter to the operator.
      const rules = item.matched_rules ?? [];
      if (rules.length === 0) {
        return <span className="text-muted-2">—</span>;
      }
      const shown = rules.slice(0, 3);
      const overflow = rules.length - shown.length;
      return (
        <div className="flex flex-wrap gap-1">
          {shown.map((r) => (
            <Pill key={r.rule_id} sev={r.severity}>
              {r.rule_name}
            </Pill>
          ))}
          {overflow > 0 ? (
            <Pill aria-label={`${overflow} more matched rules`}>
              +{overflow}
            </Pill>
          ) : null}
        </div>
      );
    }
    case "tags": {
      // Stage 13 (audit follow-up): first three tag names as
      // chips with a ``+N`` overflow indicator. Tags come back
      // sorted alphabetically (the backend ORDER BYs name); the
      // first three are arbitrary-but-deterministic. The drawer
      // surfaces the full source-grouped view when an operator
      // wants the detail.
      const tags = item.tags ?? [];
      if (tags.length === 0) {
        return <span className="text-muted-2">—</span>;
      }
      const shown = tags.slice(0, 3);
      const overflow = tags.length - shown.length;
      return (
        <div className="flex flex-wrap gap-1">
          {shown.map((t) => (
            <Tag key={t}>{t}</Tag>
          ))}
          {overflow > 0 ? (
            <Tag aria-label={`${overflow} more tags`}>+{overflow}</Tag>
          ) : null}
        </div>
      );
    }
  }
}

function Checkbox({
  checked,
  indeterminate,
  onChange,
  ...rest
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: () => void;
} & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "onChange" | "checked" | "type"
>) {
  return (
    <input
      type="checkbox"
      checked={checked}
      ref={(el) => {
        if (el) el.indeterminate = !!indeterminate && !checked;
      }}
      onChange={onChange}
      className="files-checkbox"
      {...rest}
    />
  );
}
