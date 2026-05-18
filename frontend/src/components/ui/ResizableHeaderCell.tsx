/**
 * Stage 03 — shared resizable column-header primitive.
 *
 * Extracted from ``FilesTable.tsx``'s inline ``ResizeHandle``.
 * Identical contract: drag the handle at the right edge of a
 * ``<th>`` to resize the matching ``<col>`` in the closest
 * ``<colgroup>``. Pointer events cover mouse, touch, and pen in
 * one path (per addendum C.2 — no parallel touch handling). The
 * handle uses ``setPointerCapture`` so the operator can drag past
 * the edge without losing the gesture.
 *
 * The primitive is intentionally **handle-only** rather than a
 * full ``<th>`` wrapper:
 *
 *   1. Existing tests across both FilesTable and (future)
 *      RulesTable assert on the ``<th>`` shape — class names,
 *      ARIA attributes, sort-indicator children. A wrapper would
 *      force every test-mount to adopt the wrapper's DOM, which
 *      multiplies refactor cost.
 *   2. Two tables, two different vocabularies (FilesColumnKey,
 *      RulesColumnKey). A handle-only primitive takes a generic
 *      string key and stays decoupled from each store.
 *
 * The handle finds the matching ``<col>`` via
 * ``col[data-col-key="<key>"]`` in the closest ancestor table.
 * Both consumers' colgroups must set ``data-col-key`` for this
 * lookup to work.
 *
 * Both class names ``files-th-resizer`` and ``ui-th-resizer`` are
 * emitted on the rendered element. The first is the legacy name
 * Stage 02 introduced; the second is the canonical Stage-03 name.
 * CSS in ``components.css`` targets both via a combined selector
 * so the two are interchangeable.
 */

import { useCallback, useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

export interface ResizableHeaderCellProps {
  /** Unique key for the column. The handle locates the matching
   *  ``<col data-col-key="...">`` element by this key. */
  columnKey: string;
  /** Width the operator started the gesture from (px). */
  currentWidth: number;
  /** Minimum width the gesture clamps to (px). */
  minWidth?: number;
  /** Commits a new width when the gesture ends. */
  onCommit: (key: string, width: number) => void;
  /** Optional override of the resize-handle's accessible label.
   *  Default deliberately avoids the substring "resize" / "size"
   *  so callers' existing testing-library lookups stay
   *  unambiguous (see Stage 02 STAGE_NOTES). */
  ariaLabel?: string;
}

export function ResizableHeaderCell({
  columnKey,
  currentWidth,
  minWidth = 48,
  onCommit,
  ariaLabel,
}: ResizableHeaderCellProps) {
  const startX = useRef<number>(0);
  const startWidth = useRef<number>(currentWidth);
  const colEl = useRef<HTMLElement | null>(null);
  const liveWidth = useRef<number>(currentWidth);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLSpanElement>) => {
      e.stopPropagation();
      e.preventDefault();
      const handle = e.currentTarget;
      const table = handle.closest("table");
      const col = table?.querySelector<HTMLElement>(
        `col[data-col-key="${columnKey}"]`,
      );
      colEl.current = col ?? null;
      startX.current = e.clientX;
      startWidth.current = currentWidth;
      liveWidth.current = currentWidth;
      handle.setPointerCapture(e.pointerId);
      // v1.9 Stage 3.2 — paint a global col-resize cursor for
      // the duration of the gesture so the operator can drag past
      // the table's edge without losing the visual affordance.
      // CSS in components.css picks up the body attribute and
      // forces ``cursor: col-resize`` on every descendant.
      if (typeof document !== "undefined") {
        document.body.dataset.resizingCol = "1";
      }
    },
    [columnKey, currentWidth],
  );

  const onPointerMove = useCallback(
    (e: ReactPointerEvent<HTMLSpanElement>) => {
      const handle = e.currentTarget;
      if (!handle.hasPointerCapture(e.pointerId)) return;
      const delta = e.clientX - startX.current;
      const next = Math.max(minWidth, Math.round(startWidth.current + delta));
      liveWidth.current = next;
      if (colEl.current) {
        colEl.current.style.width = `${next}px`;
      }
    },
    [minWidth],
  );

  const finish = useCallback(
    (e: ReactPointerEvent<HTMLSpanElement>) => {
      const handle = e.currentTarget;
      if (handle.hasPointerCapture(e.pointerId)) {
        handle.releasePointerCapture(e.pointerId);
      }
      // v1.9 Stage 3.2 — clear the global drag-cursor flag.
      if (typeof document !== "undefined") {
        delete document.body.dataset.resizingCol;
      }
      if (liveWidth.current !== startWidth.current) {
        onCommit(columnKey, liveWidth.current);
      }
    },
    [columnKey, onCommit],
  );

  return (
    <span
      // Both class names so legacy CSS targeting either keeps working.
      className="files-th-resizer ui-th-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel ?? `Adjust ${columnKey} column width`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onClick={(e) => {
        // Block click bubbling — otherwise the th's onClick would
        // interpret pointerup as "sort by this column".
        e.stopPropagation();
      }}
    />
  );
}
