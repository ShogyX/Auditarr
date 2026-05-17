/**
 * Stage 02 — column resize commits the new width to the prefs store.
 *
 * Renders the FilesTable with a minimal mock query result and
 * simulates a pointerdown / pointermove / pointerup sequence on
 * the resize handle of the Size column. After the gesture, the
 * persisted store width should equal the dragged-to value.
 *
 * The test mounts ``<FilesTable>`` directly (not the whole
 * ``<FilesPage>``) because the resize behaviour is local to the
 * table and we don't need the route / scope-bar / toolbar context
 * to exercise it.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render } from "@testing-library/react";

import { FilesTable } from "./FilesTable";
import {
  useFilesPrefs,
  effectiveColumnWidth,
} from "@/stores/filesPrefsStore";

function makeListMock() {
  return {
    isLoading: false,
    isError: false,
    data: {
      items: [
        {
          id: "f1",
          library_id: "lib1",
          filename: "A.mkv",
          path: "/lib/A.mkv",
          extension: ".mkv",
          category: "media",
          severity: "ok",
          size_bytes: 1_000_000,
          mtime: "2025-01-01T00:00:00Z",
          video_codec: "h264",
          audio_codec: "aac",
          container: "matroska",
          width: 1920,
          height: 1080,
          has_subtitles: false,
          is_orphaned: false,
          matched_rules: [],
          tags: [],
        },
      ],
      total: 1,
      offset: 0,
      limit: 50,
    },
    error: undefined,
  };
}

describe("FilesTable — Stage 02 column resize", () => {
  beforeEach(() => {
    // Reset the persisted store so each test starts from defaults.
    useFilesPrefs.setState({
      columnWidths: {},
      perColumnFilters: {},
    });
    // Make sure localStorage doesn't leak between tests.
    window.localStorage.clear();
  });

  it("commits a new width on pointerup", () => {
    const noop = () => {};
    const { container } = render(
      <FilesTable
        list={makeListMock() as never}
        visibleColumns={["filename", "category", "severity", "size", "codec"]}
        sort={{ key: "severity", dir: "desc" }}
        onSort={noop}
        selected={new Set()}
        onToggleSel={noop}
        onToggleAll={noop}
        allVisibleSelected={false}
        someVisibleSelected={false}
        onOpenDrawer={noop}
        columnWidths={{}}
        onColumnResize={(key, width) => {
          useFilesPrefs.getState().setColumnWidth(key, width);
        }}
        perColumnFilters={{}}
        onPerColumnFilterChange={noop}
        showColumnFilters={false}
      />,
    );

    // Find the resize handle on the size column.
    const handle = container.querySelector<HTMLSpanElement>(
      'span[aria-label="Adjust size column width"]',
    );
    expect(handle, "size column resize handle must render").not.toBeNull();
    if (!handle) return;

    // JSDOM doesn't implement setPointerCapture / hasPointerCapture
    // — stub them so the handler logic completes. Same trick used
    // by the React Testing Library docs for pointer-event work.
    // We track whether capture is "active" so the move handler's
    // guard succeeds.
    let captured = false;
    handle.setPointerCapture = () => {
      captured = true;
    };
    handle.releasePointerCapture = () => {
      captured = false;
    };
    handle.hasPointerCapture = () => captured;

    // JSDOM's PointerEvent constructor doesn't pick up clientX
    // from the event-init dict (it inherits from Event, not
    // MouseEvent, in some versions). Construct events through
    // MouseEvent and dispatch, with the pointerId attached post
    // hoc — that's the path React's synthetic-event system reads
    // those values from anyway.
    function dispatchPointer(
      type: "pointerdown" | "pointermove" | "pointerup",
      clientX: number,
      pointerId = 1,
    ) {
      // Use Event with manually-set properties so the values
      // round-trip through React's synthetic-event wrapper.
      const ev = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "clientX", { value: clientX });
      Object.defineProperty(ev, "clientY", { value: 0 });
      Object.defineProperty(ev, "pointerId", { value: pointerId });
      Object.defineProperty(ev, "button", { value: 0 });
      handle!.dispatchEvent(ev);
    }

    dispatchPointer("pointerdown", 500);
    dispatchPointer("pointermove", 580);
    dispatchPointer("pointerup", 580);

    const stored = useFilesPrefs.getState().columnWidths;
    expect(stored.size).toBe(176);
    // The default for size is 96; effectiveColumnWidth must read
    // back the new value.
    expect(effectiveColumnWidth("size", stored)).toBe(176);
  });

  it("does not commit a width when the operator never moved the pointer", () => {
    const noop = () => {};
    let commits = 0;
    const { container } = render(
      <FilesTable
        list={makeListMock() as never}
        visibleColumns={["filename", "severity", "size"]}
        sort={{ key: "severity", dir: "desc" }}
        onSort={noop}
        selected={new Set()}
        onToggleSel={noop}
        onToggleAll={noop}
        allVisibleSelected={false}
        someVisibleSelected={false}
        onOpenDrawer={noop}
        columnWidths={{}}
        onColumnResize={() => {
          commits += 1;
        }}
        perColumnFilters={{}}
        onPerColumnFilterChange={noop}
        showColumnFilters={false}
      />,
    );
    const handle = container.querySelector<HTMLSpanElement>(
      'span[aria-label="Adjust size column width"]',
    );
    if (!handle) throw new Error("handle missing");
    const h = handle; // narrow for the inner closure
    let captured = false;
    h.setPointerCapture = () => {
      captured = true;
    };
    h.releasePointerCapture = () => {
      captured = false;
    };
    h.hasPointerCapture = () => captured;

    function dispatchPointer(
      type: "pointerdown" | "pointermove" | "pointerup",
      clientX: number,
      pointerId = 2,
    ) {
      const ev = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "clientX", { value: clientX });
      Object.defineProperty(ev, "clientY", { value: 0 });
      Object.defineProperty(ev, "pointerId", { value: pointerId });
      Object.defineProperty(ev, "button", { value: 0 });
      h.dispatchEvent(ev);
    }
    dispatchPointer("pointerdown", 400);
    dispatchPointer("pointerup", 400);
    expect(commits, "no-move pointerup should not commit a new width").toBe(0);
  });
});
