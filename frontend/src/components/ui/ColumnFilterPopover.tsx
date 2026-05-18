/**
 * v1.9 Stage 3.1 — per-column filter popover.
 *
 * One generalized popover replaces the Stage-31 CodecFilterMenu +
 * ContainerFilterMenu pattern. Each filterable column header
 * renders a small filter icon (the trigger); clicking opens this
 * popover. Inside:
 *
 *   * Search input — case-insensitive prefix match. Debounced
 *     250ms before firing the next ``/media/distinct?prefix=``
 *     fetch so typing doesn't hammer the server.
 *   * Include / Exclude mode toggle. Pre-1.9 the codec filter was
 *     include-only ("show me HEVC files"); the new toggle lets
 *     operators say "show me everything EXCEPT JPEG" with one
 *     click.
 *   * Scrollable list of checkboxes — value + count from the
 *     backend. ``(none)`` row for the NULL bucket.
 *   * Truncation hint when more than 200 distinct values exist.
 *   * Clear + Apply / Close buttons in the footer.
 *
 * Selection lives in the parent so the URL query string stays
 * the single source of truth. The popover emits ``onToggle`` per
 * value and ``onModeChange`` for the include/exclude switch.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { LoadingState, EmptyState } from "@/components/ui/States";
import { useMediaDistinct } from "@/hooks/useMediaDistinct";

export type FilterMode = "include" | "exclude";

export interface ColumnFilterPopoverProps {
  /** The backend column key — must be in the
   *  ``DISTINCT_FIELDS`` whitelist. The popover surfaces backend
   *  errors verbatim if it isn't. */
  field: string;
  /** Operator-facing column label, used in the popover header
   *  and the trigger button's aria-label. */
  label: string;
  /** The current selection. Empty set = no filter active. */
  selected: Set<string>;
  mode: FilterMode;
  /** Optional library scope (forwards to the distinct endpoint). */
  libraryId?: string | null;
  onToggle: (value: string) => void;
  onModeChange: (mode: FilterMode) => void;
  onClear: () => void;
}

export function ColumnFilterPopover({
  field,
  label,
  selected,
  mode,
  libraryId = null,
  onToggle,
  onModeChange,
  onClear,
}: ColumnFilterPopoverProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Debounce the search input so each keystroke doesn't fire a
  // new distinct fetch. 250ms is the same lag the rest of the
  // app uses for incremental filtering.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  // Suspend the query while the popover is closed.
  const distinct = useMediaDistinct(field, {
    libraryId,
    prefix: debouncedSearch || null,
    enabled: open,
  });

  // Click-outside + Escape close.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Active filter count surfaces on the trigger as a small badge,
  // matching the existing Columns + CodecFilterMenu visual.
  const activeCount = selected.size;

  // Sort: backend already orders by count desc, value asc. But
  // selected items should pin to the top so the operator can
  // see their current selection without scrolling.
  const rows = useMemo(() => {
    const values = distinct.data?.values ?? [];
    const sel = new Set(selected);
    const inSel: typeof values = [];
    const rest: typeof values = [];
    for (const v of values) {
      const key = v.value ?? "(none)";
      if (sel.has(key)) inSel.push(v);
      else rest.push(v);
    }
    return [...inSel, ...rest];
  }, [distinct.data, selected]);

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-muted-2 hover:text-text hover:bg-[var(--hover)]"
        aria-label={`Filter ${label}`}
        title={`Filter ${label}`}
        aria-expanded={open}
      >
        <Icon name="filter" size={11} />
        {activeCount > 0 ? (
          <span className="text-[10px] font-mono">{activeCount}</span>
        ) : null}
      </button>

      {open ? (
        <div
          className="absolute z-30 mt-1 left-0 min-w-[240px] max-w-[320px] rounded-md border border-border bg-surface shadow-lg"
          role="dialog"
          aria-label={`Filter ${label}`}
        >
          {/* Header: title + clear */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between gap-2">
            <div className="text-[12px] font-semibold">{label}</div>
            {activeCount > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={onClear}
                title="Clear this column's filter"
              >
                Clear
              </Button>
            ) : null}
          </div>

          {/* Include / Exclude toggle */}
          <div
            className="px-3 py-2 border-b border-border flex items-center gap-2 text-[12px]"
            role="radiogroup"
            aria-label="Filter mode"
          >
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="radio"
                name={`filter-mode-${field}`}
                checked={mode === "include"}
                onChange={() => onModeChange("include")}
              />
              <span>Include</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="radio"
                name={`filter-mode-${field}`}
                checked={mode === "exclude"}
                onChange={() => onModeChange("exclude")}
              />
              <span>Exclude</span>
            </label>
          </div>

          {/* Search */}
          <div className="px-3 py-2 border-b border-border">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search…"
              className="w-full px-2 h-7 rounded-[6px] bg-surface-sunk border border-border text-[12px]"
              aria-label="Filter values"
              autoFocus
            />
          </div>

          {/* Body: scrollable checkbox list */}
          <div className="max-h-64 overflow-y-auto px-2 py-1">
            {distinct.isLoading ? (
              <div className="px-2 py-3">
                <LoadingState label="Loading values…" />
              </div>
            ) : distinct.isError ? (
              <div className="px-2 py-3 text-[12px] text-sev-error">
                {(distinct.error as Error)?.message ?? "Failed to load"}
              </div>
            ) : rows.length === 0 ? (
              <div className="px-2 py-3">
                <EmptyState
                  icon="filter"
                  title="No matching values"
                  description={
                    debouncedSearch
                      ? "Try a different search term."
                      : "No data in this column yet."
                  }
                />
              </div>
            ) : (
              <ul className="list-none m-0 p-0">
                {rows.map((row) => {
                  const key = row.value ?? "(none)";
                  const isSelected = selected.has(key);
                  return (
                    <li key={key}>
                      <label className="flex items-center gap-2 px-2 py-1 cursor-pointer hover:bg-[var(--hover)] rounded text-[12px]">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => onToggle(key)}
                        />
                        <span className="flex-1 truncate" title={key}>
                          {row.value === null ? (
                            <span className="italic text-muted-2">(none)</span>
                          ) : (
                            key
                          )}
                        </span>
                        <span className="text-muted-2 text-[11px] font-mono">
                          {row.count}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Truncation hint */}
          {distinct.data?.truncated ? (
            <div className="px-3 py-1.5 border-t border-border text-[11px] text-muted-2">
              More than 200 distinct values — refine search.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
