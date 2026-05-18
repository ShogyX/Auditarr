/**
 * Stage 3 — Files table.
 *
 * Stage 02 (v1.7) extends this with:
 *
 *   - A ``<colgroup>`` element with one ``<col>`` per visible column.
 *     The table is now ``table-layout: fixed`` (set in
 *     components.css), so widths come exclusively from the colgroup.
 *     This is what makes the resize math behave: auto-layout tables
 *     re-flow on every cell, fixed-layout tables honour explicit
 *     column widths.
 *
 *   - A drag-to-resize handle on every ``<th>``. Implementation is
 *     inline (no new library) using pointer events — one path that
 *     covers mouse, touch, and pen alike (per plan addendum C.2).
 *     The handle uses ``setPointerCapture`` so the operator can
 *     drag past the column edge without the gesture jumping.
 *
 *   - An optional per-column quick-filter row, rendered when the
 *     toolbar's filter toggle is on. The store carries the values
 *     under ``perColumnFilters``; the parent (useFilesPageState)
 *     pipes them into ``useMediaList`` as new query params.
 *
 *   - Severity column: the Pill now resolves through
 *     ``sevToClass`` (which now includes ``crit``), giving the cell
 *     the same colour as the scope-bar swatch.
 *
 * Pre-Stage-02 invariants preserved exactly:
 *
 *   - ``<table class="files-table" role="grid">``
 *   - ``<th class="is-sortable is-sorted num">`` with aria-sort
 *   - ``<tr class="files-table-row is-selected">``
 *   - ``<td class="num">`` for numeric columns
 *   - ``.files-table-sort-ind`` arrows ``↑ / ↓ / ↕``
 *
 * So the 30+ existing FilesPage/FilesTable tests keep passing.
 */

import type { InputHTMLAttributes } from "react";

import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import { ResizableHeaderCell } from "@/components/ui/ResizableHeaderCell";
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
  FILES_COLUMN_MIN_WIDTH,
  effectiveColumnWidth,
  type FilesColumnKey,
} from "@/stores/filesPrefsStore";

/** Columns whose per-column filter input is wired to the backend. */
const FILTERABLE_COLUMN_KEYS: ReadonlySet<FilesColumnKey> = new Set<FilesColumnKey>([
  "filename",
  "codec",
  "container",
  "extension",
]);

/** Placeholder text shown in the per-column filter input. */
const FILTER_PLACEHOLDERS: Partial<Record<FilesColumnKey, string>> = {
  filename: "filter by path…",
  codec: "e.g. hevc",
  container: "e.g. matroska",
  extension: "e.g. .mkv",
};

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
  /** Stage 02 — resolved column widths (px). */
  columnWidths: Partial<Record<FilesColumnKey, number>>;
  /** Stage 02 — commit a new width on resize-end. */
  onColumnResize: (key: FilesColumnKey, width: number) => void;
  /** Stage 02 — per-column filter inputs. Empty/absent → no filter. */
  perColumnFilters: Partial<Record<FilesColumnKey, string>>;
  /** Stage 02 — write a per-column filter value. */
  onPerColumnFilterChange: (key: FilesColumnKey, value: string) => void;
  /** Stage 02 — whether to render the per-column filter row at all. */
  showColumnFilters: boolean;
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
  columnWidths,
  onColumnResize,
  perColumnFilters,
  onPerColumnFilterChange,
  showColumnFilters,
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
        <colgroup>
          {/* Stage 02 — the leading checkbox col is fixed-width so
              its visual weight stays constant regardless of how the
              operator resized other columns. */}
          <col className="files-table-check-col" style={{ width: 36 }} />
          {cols.map((c) => {
            const key = c.key as FilesColumnKey;
            const width = effectiveColumnWidth(key, columnWidths);
            return <col key={key} style={{ width }} data-col-key={key} />;
          })}
        </colgroup>
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
              const key = c.key as FilesColumnKey;
              const sortKey = "sortKey" in c ? c.sortKey : undefined;
              const isSorted = !!sortKey && sort.key === sortKey;
              return (
                <th
                  key={key}
                  className={cn(
                    sortKey && "is-sortable",
                    isSorted && "is-sorted",
                    "num" in c && c.num && "num",
                  )}
                  // v1.9 Stage 3.2 — flag that this header has a
                  // resize handle so the CSS in components.css
                  // can paint the 1px hover affordance.
                  data-col-resizable="1"
                  onClick={
                    sortKey
                      ? (e) => {
                          // Don't fire sort when the click originated
                          // from the resize handle (pointer-up bubbles).
                          const cls = (e.target as HTMLElement).classList;
                          if (
                            cls.contains("ui-th-resizer") ||
                            cls.contains("files-th-resizer")
                          ) {
                            return;
                          }
                          onSort(sortKey);
                        }
                      : undefined
                  }
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
                  <ResizableHeaderCell
                    columnKey={key}
                    currentWidth={effectiveColumnWidth(key, columnWidths)}
                    minWidth={FILES_COLUMN_MIN_WIDTH}
                    onCommit={(k, w) =>
                      onColumnResize(k as FilesColumnKey, w)
                    }
                  />
                </th>
              );
            })}
          </tr>
          {showColumnFilters ? (
            <tr className="files-table-filter-row">
              <th className="files-table-check" aria-hidden="true" />
              {cols.map((c) => {
                const key = c.key as FilesColumnKey;
                if (!FILTERABLE_COLUMN_KEYS.has(key)) {
                  return <th key={key} aria-hidden="true" />;
                }
                const value = perColumnFilters[key] ?? "";
                return (
                  <th key={key}>
                    <input
                      type="text"
                      className="files-th-filter"
                      placeholder={FILTER_PLACEHOLDERS[key] ?? "filter…"}
                      value={value}
                      aria-label={`Filter ${c.label}`}
                      onChange={(e) =>
                        onPerColumnFilterChange(key, e.target.value)
                      }
                    />
                  </th>
                );
              })}
            </tr>
          ) : null}
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

/**
 * Stage 02 — pointer-event-based resize handle.
 *
 * Stage 03 — extracted to ``@/components/ui/ResizableHeaderCell``
 * and shared between FilesTable and RulesTable. The Stage 02
 * implementation lived inline here; the new home preserves the
 * same contract (pointer events, ``setPointerCapture``,
 * data-col-key lookup against the closest table).
 */
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
            {/* Stage 27 rendered a "Quarantined" Pill here; Stage 05
                (v1.7) removed it along with the quarantine workflow. */}
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
