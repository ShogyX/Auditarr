/**
 * v1.9 Stage 7.1 — StringChipEditor.
 *
 * Pins:
 *   1. Renders existing items as chips.
 *   2. Typing + Enter adds a trimmed entry.
 *   3. Typing + clicking Add adds the entry.
 *   4. Empty / whitespace-only input is ignored.
 *   5. Duplicates (case-insensitive) are silently skipped.
 *   6. Clicking × on a chip removes that entry only.
 *   7. Without onAutoDiscover, the discover button is hidden.
 *   8. With onAutoDiscover, suggestions render as clickable
 *      pills; clicking adds them.
 *   9. Suggestions already present in the value list are
 *      filtered out of the suggestion display.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { StringChipEditor } from "@/features/integrations/StringChipEditor";

function Wrapper({
  initial = [],
  onAutoDiscover,
}: {
  initial?: string[];
  onAutoDiscover?: () => Promise<string[]>;
}) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <StringChipEditor
      value={value}
      onChange={setValue}
      onAutoDiscover={onAutoDiscover}
      ariaLabel="test-chip-input"
    />
  );
}

describe("StringChipEditor", () => {
  it("renders existing items as chips", () => {
    render(<Wrapper initial={["a", "b", "c"]} />);
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
  });

  it("commits a trimmed entry on Enter", () => {
    render(<Wrapper />);
    const input = screen.getByLabelText("test-chip-input");
    fireEvent.change(input, { target: { value: "  10.0.0.5  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("commits an entry when Add is clicked", () => {
    render(<Wrapper />);
    const input = screen.getByLabelText("test-chip-input");
    fireEvent.change(input, { target: { value: "sonarr.local" } });
    fireEvent.click(screen.getByRole("button", { name: /add/i }));
    expect(screen.getByText("sonarr.local")).toBeInTheDocument();
  });

  it("ignores whitespace-only input", () => {
    render(<Wrapper />);
    const input = screen.getByLabelText("test-chip-input");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    // No chips rendered.
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("silently dedupes case-insensitive duplicates", () => {
    render(<Wrapper initial={["keep"]} />);
    const input = screen.getByLabelText("test-chip-input");
    fireEvent.change(input, { target: { value: "KEEP" } });
    fireEvent.keyDown(input, { key: "Enter" });
    // Still only one chip; both casings are not separately rendered.
    // The chip's textContent includes the "×" remove glyph, so we
    // assert by looking for the original casing as the first token.
    const chip = screen.getByText("keep");
    expect(chip).toBeInTheDocument();
    expect(screen.queryByText("KEEP")).toBeNull();
  });

  it("removes a chip when × is clicked", () => {
    render(<Wrapper initial={["a", "b", "c"]} />);
    fireEvent.click(screen.getByLabelText("remove b"));
    expect(screen.queryByText("b")).toBeNull();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
  });

  it("hides the Auto-discover button when no callback supplied", () => {
    render(<Wrapper />);
    expect(
      screen.queryByRole("button", { name: /auto-discover/i }),
    ).toBeNull();
  });

  it("clicking a suggestion pill adds it to the value list", async () => {
    const onAutoDiscover = vi.fn(async () => ["alpha", "beta"]);
    render(<Wrapper onAutoDiscover={onAutoDiscover} />);
    fireEvent.click(
      screen.getByRole("button", { name: /auto-discover/i }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("string-chip-suggestions"),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /\+ alpha/i }));
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });

  it("filters suggestions already present in the value list", async () => {
    const onAutoDiscover = vi.fn(async () => ["alpha", "beta"]);
    render(<Wrapper initial={["ALPHA"]} onAutoDiscover={onAutoDiscover} />);
    fireEvent.click(
      screen.getByRole("button", { name: /auto-discover/i }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("string-chip-suggestions"),
      ).toBeInTheDocument(),
    );
    // "alpha" is already present (case-insensitive) → only beta
    // suggestion remains.
    expect(
      screen.queryByRole("button", { name: /\+ alpha/i }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: /\+ beta/i }),
    ).toBeInTheDocument();
  });
});
