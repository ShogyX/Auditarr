/**
 * v1.9 Stage 9.5.7 (OP-9) — IncompatibleMediaCard contract.
 *
 * Pins:
 *   1. count > 0 → renders count + view link
 *   2. count == 0 → hides entirely (no rule fired yet → no
 *      tile to crowd the dashboard)
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IncompatibleMediaCard } from "./IncompatibleMediaCard";

const hookMock = vi.fn();

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardIncompatibleMedia: () => hookMock(),
}));

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <IncompatibleMediaCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("IncompatibleMediaCard — v1.9 Stage 9.5.7 (OP-9)", () => {
  beforeEach(() => {
    hookMock.mockReset();
  });

  it("renders count + view link when count > 0", () => {
    hookMock.mockReturnValue({
      isLoading: false,
      data: { count: 17, sample_ids: ["x", "y"] },
    });
    renderCard();
    expect(
      screen.getByTestId("incompatible-media-card"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("incompatible-media-view-link"),
    ).toHaveAttribute("href", "/files?tag=incompatible");
    expect(screen.getByText(/17/)).toBeInTheDocument();
  });

  it("hides entirely when count is 0", () => {
    hookMock.mockReturnValue({
      isLoading: false,
      data: { count: 0, sample_ids: [] },
    });
    const { container } = renderCard();
    expect(container.firstChild).toBeNull();
  });
});
