/**
 * Stage 02 — per-column filter survives multi-select.
 *
 * Plan §177: "when the user multi-selects, do not reset
 * perColumnFilters". The store-level guarantee is structural: the
 * filter map lives in ``useFilesPrefs`` (persisted) while
 * selection is transient page state. Changing selection cannot
 * touch the prefs store unless we deliberately wire it that way.
 *
 * This test exercises the contract by:
 *   1. Setting a per-column filter via the store API.
 *   2. Rendering the FilesTable with the filter row visible.
 *   3. Selecting two rows via the row checkboxes.
 *   4. Asserting both: rows remain selected, AND the filter
 *      value is still in the store.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { FilesTable } from "./FilesTable";
import { useFilesPrefs } from "@/stores/filesPrefsStore";

function makeListMock() {
  const items = [
    {
      id: "id-A",
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
    {
      id: "id-B",
      library_id: "lib1",
      filename: "B.mkv",
      path: "/lib/B.mkv",
      extension: ".mkv",
      category: "media",
      severity: "warn",
      size_bytes: 2_000_000,
      mtime: "2025-01-02T00:00:00Z",
      video_codec: "hevc",
      audio_codec: "aac",
      container: "matroska",
      width: 3840,
      height: 2160,
      has_subtitles: true,
      is_orphaned: false,
      matched_rules: [],
      tags: [],
    },
  ];
  return {
    isLoading: false,
    isError: false,
    data: { items, total: items.length, offset: 0, limit: 50 },
    error: undefined,
  };
}

describe("FilesTable — Stage 02 per-column filter", () => {
  beforeEach(() => {
    useFilesPrefs.setState({ columnWidths: {}, perColumnFilters: {} });
    window.localStorage.clear();
  });

  it("filter input writes to the store", () => {
    const noop = () => {};
    render(
      <FilesTable
        list={makeListMock() as never}
        visibleColumns={["filename", "codec", "severity", "size"]}
        sort={{ key: "severity", dir: "desc" }}
        onSort={noop}
        selected={new Set()}
        onToggleSel={noop}
        onToggleAll={noop}
        allVisibleSelected={false}
        someVisibleSelected={false}
        onOpenDrawer={noop}
        columnWidths={{}}
        onColumnResize={noop}
        perColumnFilters={{}}
        onPerColumnFilterChange={(k, v) =>
          useFilesPrefs.getState().setPerColumnFilter(k, v)
        }
        showColumnFilters={true}
      />,
    );
    const codecFilter = screen.getByLabelText(/^Filter Codec$/i);
    fireEvent.change(codecFilter, { target: { value: "hevc" } });
    expect(useFilesPrefs.getState().perColumnFilters.codec).toBe("hevc");
  });

  it("filter persists in the store while selecting two rows", () => {
    // Seed a filter in the store first.
    useFilesPrefs.getState().setPerColumnFilter("codec", "hevc");

    // Render with controlled selection so we can drive it.
    let selected = new Set<string>();
    const onToggleSel = (id: string) => {
      const next = new Set(selected);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      selected = next;
      rerender(view(selected));
    };
    const noop = () => {};
    function view(sel: Set<string>) {
      return (
        <FilesTable
          list={makeListMock() as never}
          visibleColumns={["filename", "codec", "severity", "size"]}
          sort={{ key: "severity", dir: "desc" }}
          onSort={noop}
          selected={sel}
          onToggleSel={onToggleSel}
          onToggleAll={noop}
          allVisibleSelected={false}
          someVisibleSelected={sel.size > 0}
          onOpenDrawer={noop}
          columnWidths={{}}
          onColumnResize={noop}
          perColumnFilters={useFilesPrefs.getState().perColumnFilters}
          onPerColumnFilterChange={(k, v) =>
            useFilesPrefs.getState().setPerColumnFilter(k, v)
          }
          showColumnFilters={true}
        />
      );
    }
    const { rerender } = render(view(selected));

    fireEvent.click(screen.getByLabelText(/select a\.mkv/i));
    fireEvent.click(screen.getByLabelText(/select b\.mkv/i));

    expect(selected.has("id-A")).toBe(true);
    expect(selected.has("id-B")).toBe(true);
    // The filter is still in the store after both selections.
    expect(useFilesPrefs.getState().perColumnFilters.codec).toBe("hevc");
  });
});
