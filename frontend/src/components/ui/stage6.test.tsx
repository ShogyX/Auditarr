/**
 * Stage 6 (audit follow-up) — Sparkline + SeverityHeatmap edge cases.
 *
 * Pins:
 *   - Sparkline with one value renders a flat line across full width
 *     (no NaN, no "all points at x=0").
 *   - Sparkline with all-zero values exposes ``data-spark-empty="true"``
 *     and renders the ``emptyLabel`` when supplied.
 *   - Sparkline with normal data still works (regression guard).
 *   - SeverityHeatmap with total === 0 distributes width evenly and
 *     surfaces a "no files indexed yet" label.
 *   - SeverityHeatmap with non-zero data keeps the old flexGrow shape.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SeverityHeatmap } from "@/components/ui/SeverityHeatmap";
import { Sparkline } from "@/components/ui/Sparkline";

describe("Stage 6 — Sparkline edge cases", () => {
  it("renders a flat line for a single-value series across full width", () => {
    const { container } = render(<Sparkline values={[42]} />);
    const svg = container.querySelector("svg")!;
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("data-spark-single")).toBe("true");
    // The path must span x=0 to x=100, not "all points at x=0".
    const line = svg.querySelector("path.line")!;
    const d = line.getAttribute("d") ?? "";
    expect(d).toMatch(/^M0\s+\d/);
    expect(d).toContain("L100");
  });

  it("flags an all-zero series via data-spark-empty and renders emptyLabel", () => {
    const { container } = render(
      <Sparkline values={[0, 0, 0, 0]} emptyLabel="no data yet" />,
    );
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("data-spark-empty")).toBe("true");
    expect(screen.getByText("no data yet")).toBeInTheDocument();
  });

  it("does NOT render emptyLabel when the data is non-zero", () => {
    const { container } = render(
      <Sparkline values={[1, 2, 3]} emptyLabel="no data yet" />,
    );
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("data-spark-empty")).toBeNull();
    expect(screen.queryByText("no data yet")).not.toBeInTheDocument();
  });

  it("renders nothing for an empty series (preserves legacy behaviour)", () => {
    const { container } = render(<Sparkline values={[]} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders normal-shape path for a multi-value series", () => {
    const { container } = render(<Sparkline values={[1, 5, 3, 8]} />);
    const svg = container.querySelector("svg")!;
    const line = svg.querySelector("path.line")!;
    const d = line.getAttribute("d") ?? "";
    // Four points → "M..L..L..L.." (three L commands after the M).
    const lCount = (d.match(/L/g) ?? []).length;
    expect(lCount).toBe(3);
  });
});

const SEVERITY_DATA_EMPTY = [
  { key: "ok", label: "OK", count: 0, color: "sev-ok" },
  { key: "info", label: "Info", count: 0, color: "sev-info" },
  { key: "warn", label: "Warn", count: 0, color: "sev-warn" },
  { key: "high", label: "High", count: 0, color: "sev-high" },
  { key: "error", label: "Error", count: 0, color: "sev-error" },
  { key: "crit", label: "Crit", count: 0, color: "sev-crit" },
];

const SEVERITY_DATA_FILLED = [
  { key: "ok", label: "OK", count: 100, color: "sev-ok" },
  { key: "info", label: "Info", count: 20, color: "sev-info" },
  { key: "warn", label: "Warn", count: 10, color: "sev-warn" },
  { key: "high", label: "High", count: 5, color: "sev-high" },
  { key: "error", label: "Error", count: 2, color: "sev-error" },
  { key: "crit", label: "Crit", count: 1, color: "sev-crit" },
];

describe("Stage 6 — SeverityHeatmap edge cases", () => {
  it("renders an empty-state label when total === 0 and distributes evenly", () => {
    const { container } = render(
      <SeverityHeatmap data={SEVERITY_DATA_EMPTY} />,
    );
    const bar = container.querySelector(".heatmap")!;
    expect(bar.getAttribute("data-empty")).toBe("true");
    expect(screen.getByText(/no files indexed yet/i)).toBeInTheDocument();
    // Every cell should have flexGrow=1 (equal distribution).
    const cells = Array.from(
      bar.querySelectorAll<HTMLDivElement>(":scope > div"),
    );
    expect(cells).toHaveLength(SEVERITY_DATA_EMPTY.length);
    for (const cell of cells) {
      expect(cell.style.flexGrow).toBe("1");
    }
  });

  it("uses count-weighted flex distribution when total > 0", () => {
    const { container } = render(
      <SeverityHeatmap data={SEVERITY_DATA_FILLED} />,
    );
    const bar = container.querySelector(".heatmap")!;
    expect(bar.getAttribute("data-empty")).toBeNull();
    expect(
      screen.queryByText(/no files indexed yet/i),
    ).not.toBeInTheDocument();
    // The OK cell should have the largest flexGrow (most count).
    const cells = Array.from(
      bar.querySelectorAll<HTMLDivElement>(":scope > div"),
    );
    const okCell = cells[0]!;
    const critCell = cells[5]!;
    // OK > Crit (100 vs 1).
    expect(Number(okCell.style.flexGrow)).toBeGreaterThan(
      Number(critCell.style.flexGrow),
    );
  });

  it("legend still renders every severity row regardless of total", () => {
    render(<SeverityHeatmap data={SEVERITY_DATA_EMPTY} />);
    // The legend repeats every label. With one heatmap-cell title and
    // one legend label each, "OK" should appear twice (title + legend).
    expect(screen.getAllByText("OK").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Crit").length).toBeGreaterThanOrEqual(1);
  });
});
