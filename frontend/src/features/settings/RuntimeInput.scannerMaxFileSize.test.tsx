/**
 * Stage 02 — RuntimeInput slider variant for
 * ``scanner_max_file_size_mb``.
 *
 * Plan §182: "detect the key by name and switch to a slider
 * variant. Display value with unit suffix (< 1024 → MB; ≥ 1024 →
 * GB). Allow input via either the slider or the number field;
 * they stay in sync."
 *
 * What we verify:
 *   1. For ``scanner_max_file_size_mb``, the rendered input
 *      includes an ``<input type="range">`` (the slider).
 *   2. Below 1024 → unit is "MB" in the visible label.
 *   3. At or above 1024 → unit becomes "GB".
 *   4. The standalone ``formatFileSizeMB`` helper agrees with
 *      both branches (so the regression-guard is independent of
 *      DOM specifics).
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  RuntimeInput,
  SCANNER_MAX_FILE_SIZE_LADDER,
  formatFileSizeMB,
} from "./RuntimeInput";

function makeField(value: number, key = "scanner_max_file_size_mb") {
  return {
    key,
    label: "Maximum file size to scan",
    description: "",
    category: "scanner",
    group: null,
    type: "integer" as const,
    default: 50_000,
    options: null,
    constraints: { ge: 1, le: 102_400 },
    impact: "immediate" as const,
    sensitivity: "normal" as const,
    restart_required: false,
    requires_warning: null,
    value,
    is_override: false,
    env_default: 50_000,
  };
}

describe("RuntimeInput — Stage 02 file-size slider", () => {
  it("renders a slider for the scanner_max_file_size_mb key", () => {
    render(
      <RuntimeInput
        field={makeField(500)}
        value={500}
        onChange={() => {}}
      />,
    );
    const slider = screen.getByRole("slider");
    expect(slider).not.toBeNull();
    expect((slider as HTMLInputElement).type).toBe("range");
  });

  it("displays MB unit below 1024", () => {
    render(
      <RuntimeInput
        field={makeField(500)}
        value={500}
        onChange={() => {}}
      />,
    );
    // The displayed unit suffix is rendered next to the value.
    expect(screen.getByText("500")).not.toBeNull();
    expect(screen.getByText("MB")).not.toBeNull();
  });

  it("displays GB unit at or above 1024", () => {
    render(
      <RuntimeInput
        field={makeField(2048)}
        value={2048}
        onChange={() => {}}
      />,
    );
    // 2048 MB ⇒ "2 GB".
    expect(screen.getByText("2")).not.toBeNull();
    expect(screen.getByText("GB")).not.toBeNull();
  });

  it("does NOT render a slider for unrelated integer fields", () => {
    render(
      <RuntimeInput
        field={makeField(500, "some_other_int_field")}
        value={500}
        onChange={() => {}}
      />,
    );
    // The standard number input is rendered, not a range.
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("formatFileSizeMB renders the right unit at the 1024 boundary", () => {
    expect(formatFileSizeMB(500).unit).toBe("MB");
    expect(formatFileSizeMB(1023).unit).toBe("MB");
    expect(formatFileSizeMB(1024).unit).toBe("GB");
    expect(formatFileSizeMB(2048).unit).toBe("GB");
    expect(formatFileSizeMB(2048).value).toBe("2");
    expect(formatFileSizeMB(102400).unit).toBe("GB");
    // 100 GB is the documented ceiling — value should render as
    // "100" not "100.0" (an integer ladder step renders cleanly).
    expect(formatFileSizeMB(102400).value).toBe("100");
    // Below 1 MB should render in KB so an operator who set 0.5
    // gets a sensible display.
    expect(formatFileSizeMB(0.5).unit).toBe("KB");
  });

  it("ladder is monotonically increasing and spans the documented range", () => {
    expect(SCANNER_MAX_FILE_SIZE_LADDER[0]).toBe(1);
    expect(
      SCANNER_MAX_FILE_SIZE_LADDER[SCANNER_MAX_FILE_SIZE_LADDER.length - 1],
    ).toBe(102_400);
    for (let i = 1; i < SCANNER_MAX_FILE_SIZE_LADDER.length; i += 1) {
      expect(SCANNER_MAX_FILE_SIZE_LADDER[i]!).toBeGreaterThan(
        SCANNER_MAX_FILE_SIZE_LADDER[i - 1]!,
      );
    }
  });
});
