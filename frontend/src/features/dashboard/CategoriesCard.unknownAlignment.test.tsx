/**
 * Stage 04 — Categories card unknown-row alignment.
 *
 * Plan §268: "snapshot the rendered grid; assert ``unknown`` row
 * has the same outer grid template as a non-unknown row."
 *
 * Before this stage the ``<li>`` itself carried the grid layout.
 * Linked rows packed their four cells into a single ``<a>``
 * child (which had its own grid), while ``unknown`` rows put the
 * four cells directly in the li grid — different column origins
 * for the two row types, visible offset.
 *
 * After the fix, both row types render their cells inside an
 * inner element (``.categories-row-link`` for linked,
 * ``.categories-row-static`` for unknown) that carries the
 * shared grid template. The li is just a marker.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CategoriesCard } from "./CategoriesCard";

// Stub the data hook so we get a deterministic two-row dataset.
vi.mock("@/hooks/useDashboard", () => ({
  useDashboardCategories: () => ({
    isLoading: false,
    isError: false,
    error: undefined,
    data: [
      {
        group: "video_codec",
        key: "hevc",
        label: "hevc",
        file_count: 100,
        total_size_bytes: 500_000_000_000,
      },
      {
        group: "video_codec",
        key: "unknown",
        label: "unknown",
        file_count: 50,
        total_size_bytes: 50_000_000_000,
      },
    ],
  }),
}));

function renderCategoriesCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CategoriesCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CategoriesCard — Stage 04 unknown-row alignment", () => {
  beforeEach(() => {
    // Reset persisted dashboardHidden between tests so the card is
    // visible. (uiStore persists; clear localStorage.)
    window.localStorage.clear();
  });

  it("both linked and unknown rows render inside an inner grid element", () => {
    const { container } = renderCategoriesCard();
    // Linked row (hevc) → li > a.categories-row-link with body inside.
    const linked = container.querySelector("a.categories-row-link");
    expect(linked, "linked row should render a categories-row-link").not.toBeNull();
    // Unknown row → li > div.categories-row-static with body inside.
    const staticRow = container.querySelector("div.categories-row-static");
    expect(
      staticRow,
      "unknown row should render a categories-row-static wrapper",
    ).not.toBeNull();
  });

  it("the categories-row-static inner wrapper carries the same children as a linked row", () => {
    const { container } = renderCategoriesCard();
    const linked = container.querySelector("a.categories-row-link");
    const staticRow = container.querySelector("div.categories-row-static");
    if (!linked || !staticRow) throw new Error("rows missing");

    // Both inner elements should host the same four children classes
    // — label, bar, num, count — in the same order. That's the
    // contract that makes the grid columns line up.
    function describeChildren(el: Element): string[] {
      return Array.from(el.children).map(
        (c) => `${c.tagName.toLowerCase()}.${c.className.split(" ")[0] ?? ""}`,
      );
    }
    expect(describeChildren(staticRow)).toEqual(describeChildren(linked));
  });

  it("the outer <li> is the same class on both row variants", () => {
    const { container } = renderCategoriesCard();
    const lis = container.querySelectorAll("li.categories-row");
    expect(lis.length).toBe(2);
    // Both <li>s have the same class set — neither carries a
    // variant-specific marker that would diverge the layout.
    for (const li of Array.from(lis)) {
      expect(li.className).toBe("categories-row");
    }
  });
});
