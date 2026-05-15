/**
 * Stage 1 — DataGrid primitive.
 *
 * Canonical table built on ``@tanstack/react-table``. Honours the design
 * package ``.tbl`` contract:
 *   - 13px body, 11px uppercase tracked headers, --muted header colour
 *   - Header background = --surface-2; rows sit on --surface
 *   - 10/14 header padding, 11/14 row padding
 *   - Hover row = --hover
 *   - Last row drops the bottom border
 *
 * The audit's component inventory flagged 5 feature pages with hand-rolled
 * tables: this primitive replaces all of them. ``FilesPage`` (1318 LOC) is
 * the primary consumer and the Stage 3 target.
 *
 * Density:
 *   - ``density="comfortable"`` (default) → --row-h
 *   - ``density="compact"``               → --row-h-dense
 *
 * Sort + column-visibility state are owned by the caller and threaded
 * through via ``state`` + ``onStateChange``. This keeps the primitive
 * pure-render-friendly and lets ``filesPrefsStore`` continue to own
 * persistence in Stage 3.
 *
 * Usage:
 *
 *   const columns = useMemo<ColumnDef<File>[]>(() => [
 *     { id: 'name', header: 'Name', accessorKey: 'name' },
 *     { id: 'size', header: 'Size', accessorKey: 'size',
 *       cell: ({ getValue }) => <span className="num">{formatBytes(getValue())}</span> },
 *   ], []);
 *
 *   <DataGrid
 *     data={files}
 *     columns={columns}
 *     sorting={sorting}
 *     onSortingChange={setSorting}
 *     getRowId={(row) => row.id}
 *     onRowClick={(row) => openDrawer(row)}
 *   />
 */

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Row,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { LoadingState, EmptyState, ErrorState } from "@/components/ui/States";
import { cn } from "@/lib/cn";

export type DataGridDensity = "comfortable" | "compact";

export interface DataGridProps<TData> {
  data: TData[];
  columns: readonly ColumnDef<TData, unknown>[];
  getRowId?: (row: TData, index: number) => string;
  density?: DataGridDensity;
  /** Externally-controlled sort. Omit for an unsorted grid. */
  sorting?: SortingState;
  onSortingChange?: (state: SortingState) => void;
  /** Externally-controlled column visibility. */
  columnVisibility?: VisibilityState;
  onColumnVisibilityChange?: (state: VisibilityState) => void;
  /** Row click → enables hover styling and pointer cursor on rows. */
  onRowClick?: (row: TData) => void;
  /** Optional row-level highlight predicate (e.g. selection). */
  isRowSelected?: (row: TData) => boolean;
  loading?: boolean;
  error?: ReactNode;
  /** Rendered when ``data`` is empty and ``!loading``. */
  empty?: ReactNode;
  className?: string;
}

const DENSITY_ROW: Record<DataGridDensity, string> = {
  comfortable: "h-row-h",
  compact: "h-row-h-dense",
};

export function DataGrid<TData>({
  data,
  columns,
  getRowId,
  density = "comfortable",
  sorting,
  onSortingChange,
  columnVisibility,
  onColumnVisibilityChange,
  onRowClick,
  isRowSelected,
  loading = false,
  error,
  empty,
  className,
}: DataGridProps<TData>) {
  const table = useReactTable<TData>({
    data,
    columns: columns as ColumnDef<TData, unknown>[],
    state: {
      ...(sorting ? { sorting } : {}),
      ...(columnVisibility ? { columnVisibility } : {}),
    },
    getRowId,
    onSortingChange:
      onSortingChange != null
        ? (updater) => {
            const next =
              typeof updater === "function" ? updater(sorting ?? []) : updater;
            onSortingChange(next);
          }
        : undefined,
    onColumnVisibilityChange:
      onColumnVisibilityChange != null
        ? (updater) => {
            const next =
              typeof updater === "function"
                ? updater(columnVisibility ?? {})
                : updater;
            onColumnVisibilityChange(next);
          }
        : undefined,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: sorting ? getSortedRowModel() : undefined,
    manualSorting: false,
    enableSortingRemoval: true,
  });

  if (error) {
    return (
      <ErrorState
        title="Could not load data"
        description={typeof error === "string" ? error : undefined}
      />
    );
  }

  return (
    <div className={cn("w-full overflow-x-auto", className)}>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} className="h-header-row-h">
              {headerGroup.headers.map((header) => {
                const canSort = header.column.getCanSort();
                const sortDir = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={
                      sortDir === "asc"
                        ? "ascending"
                        : sortDir === "desc"
                          ? "descending"
                          : "none"
                    }
                    className={cn(
                      "px-3.5 py-2 text-left font-medium",
                      "text-[11px] tracking-[0.04em] uppercase text-muted",
                      "bg-surface-2 border-b border-border whitespace-nowrap select-none",
                      canSort && "cursor-pointer hover:text-text",
                    )}
                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                  >
                    <span className="inline-flex items-center gap-1">
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                      {canSort ? (
                        <span aria-hidden className={cn("text-[10px]", sortDir ? "text-text" : "text-muted-2")}>
                          {sortDir === "desc" ? (
                            <Icon name="chev_down" size={12} />
                          ) : sortDir === "asc" ? (
                            <Icon name="chev_up" size={12} />
                          ) : (
                            <span className="font-mono">·</span>
                          )}
                        </span>
                      ) : null}
                    </span>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={table.getAllLeafColumns().length} className="p-0">
                <LoadingState />
              </td>
            </tr>
          ) : table.getRowModel().rows.length === 0 ? (
            <tr>
              <td colSpan={table.getAllLeafColumns().length} className="p-0">
                {empty ?? (
                  <EmptyState title="No results" description="Adjust filters and try again." />
                )}
              </td>
            </tr>
          ) : (
            table.getRowModel().rows.map((row: Row<TData>) => {
              const selected = isRowSelected?.(row.original) ?? false;
              return (
                <tr
                  key={row.id}
                  data-selected={selected || undefined}
                  className={cn(
                    DENSITY_ROW[density],
                    "border-b border-border last:border-b-0 transition-colors",
                    onRowClick && "cursor-pointer",
                    "hover:bg-[var(--hover)]",
                    "data-[selected]:bg-[var(--active)]",
                  )}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={cn(
                        "px-3.5 align-middle text-text",
                        density === "compact" ? "py-1.5" : "py-2.5",
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
