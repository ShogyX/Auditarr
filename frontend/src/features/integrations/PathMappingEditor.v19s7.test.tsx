/**
 * v1.9 Stage 7.1 — PathMappingEditor.
 *
 * Pins:
 *   1. Empty state hint when value is [].
 *   2. Renders existing rows with from/to inputs.
 *   3. "Add mapping" appends a blank row.
 *   4. Editing a cell calls onChange with the mutated row.
 *   5. Removing a row drops it from the value list.
 *   6. Without onAutoDiscover, the Auto-discover button is
 *      hidden.
 *   7. With onAutoDiscover, clicking it surfaces suggestions
 *      and "Apply all" appends them to the value list.
 *   8. A failing discover callback shows an inline error.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  PathMappingEditor,
  type PathMappingRow,
  type PathMappingSuggestion,
} from "@/features/integrations/PathMappingEditor";

function Wrapper({
  initial = [],
  onAutoDiscover,
}: {
  initial?: PathMappingRow[];
  onAutoDiscover?: () => Promise<PathMappingSuggestion[]>;
}) {
  const [value, setValue] = useState<PathMappingRow[]>(initial);
  return (
    <PathMappingEditor
      value={value}
      onChange={setValue}
      onAutoDiscover={onAutoDiscover}
    />
  );
}

describe("PathMappingEditor", () => {
  it("renders the empty hint when value is []", () => {
    render(<Wrapper />);
    expect(screen.getByText(/no mappings configured/i)).toBeInTheDocument();
  });

  it("renders existing rows with from/to inputs", () => {
    render(
      <Wrapper
        initial={[
          { from: "/data/movies", to: "/mnt/media/Movies" },
          { from: "/data/tv", to: "/mnt/media/TV" },
        ]}
      />,
    );
    expect(
      screen.getByLabelText("path mapping 1 from"),
    ).toHaveValue("/data/movies");
    expect(
      screen.getByLabelText("path mapping 2 to"),
    ).toHaveValue("/mnt/media/TV");
  });

  it("Add mapping appends a blank row", () => {
    render(<Wrapper initial={[{ from: "/a", to: "/b" }]} />);
    fireEvent.click(screen.getByRole("button", { name: /add mapping/i }));
    expect(
      screen.getByLabelText("path mapping 2 from"),
    ).toHaveValue("");
  });

  it("editing the from cell updates the row", () => {
    const onChange = vi.fn();
    function ControlledWrapper() {
      const [value, setValue] = useState<PathMappingRow[]>([
        { from: "/a", to: "/b" },
      ]);
      return (
        <PathMappingEditor
          value={value}
          onChange={(v) => {
            setValue(v);
            onChange(v);
          }}
        />
      );
    }
    render(<ControlledWrapper />);
    fireEvent.change(screen.getByLabelText("path mapping 1 from"), {
      target: { value: "/data/movies" },
    });
    expect(onChange).toHaveBeenLastCalledWith([
      { from: "/data/movies", to: "/b" },
    ]);
  });

  it("removing a row drops it from the value list", () => {
    render(
      <Wrapper
        initial={[
          { from: "/a", to: "/b" },
          { from: "/c", to: "/d" },
        ]}
      />,
    );
    fireEvent.click(screen.getByLabelText("remove path mapping 1"));
    // Row 1 is gone; row 2 is now row 1.
    expect(
      screen.getByLabelText("path mapping 1 from"),
    ).toHaveValue("/c");
    expect(screen.queryByLabelText("path mapping 2 from")).toBeNull();
  });

  it("hides the Auto-discover button when no callback supplied", () => {
    render(<Wrapper />);
    expect(
      screen.queryByRole("button", { name: /auto-discover/i }),
    ).toBeNull();
  });

  it("Apply all appends suggestions to the value list", async () => {
    const onAutoDiscover = vi.fn(async () => [
      {
        from: "/data/tv",
        to: "/mnt/media/tv",
        confidence: "high" as const,
        library_id: "lib-1",
        library_name: "TV",
      },
      {
        from: "/data/anime",
        to: "",
        confidence: "none" as const,
        library_id: null,
        library_name: null,
      },
    ]);
    render(<Wrapper onAutoDiscover={onAutoDiscover} />);
    fireEvent.click(
      screen.getByRole("button", { name: /auto-discover/i }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("path-mapping-suggestions"),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /apply all/i }));
    // Both suggestions appended; only non-empty ``from`` keeps.
    expect(
      screen.getByLabelText("path mapping 1 from"),
    ).toHaveValue("/data/tv");
    expect(
      screen.getByLabelText("path mapping 1 to"),
    ).toHaveValue("/mnt/media/tv");
    expect(
      screen.getByLabelText("path mapping 2 from"),
    ).toHaveValue("/data/anime");
  });

  it("surfaces an inline error when discover fails", async () => {
    const onAutoDiscover = vi.fn(async () => {
      throw new Error("boom");
    });
    render(<Wrapper onAutoDiscover={onAutoDiscover} />);
    fireEvent.click(
      screen.getByRole("button", { name: /auto-discover/i }),
    );
    await waitFor(() =>
      expect(screen.getByText("boom")).toBeInTheDocument(),
    );
    // Editor still renders, value not mutated.
    expect(screen.queryByLabelText("path mapping 1 from")).toBeNull();
  });
});
