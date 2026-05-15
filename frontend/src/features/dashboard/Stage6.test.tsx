/**
 * Stage 6 (audit follow-up) — Dashboard granular hide.
 *
 * Pins: when exactly one of a paired row's cards is collapsed
 * (e.g. libraries hidden but integrations visible), the row's
 * parent grid drops from ``xl:grid-cols-2`` to ``xl:grid-cols-1``
 * so the surviving card spans the row instead of leaving a phantom
 * empty column.
 *
 * The dashboard page is large and pulls in many queries, so we
 * test the layout decision indirectly via uiStore — flip the
 * dashboardHidden array, render, then inspect the className on
 * the paired-row container.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: vi.fn(async () => null),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
    patch: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: {
      accessToken: "x",
      refreshToken: "x",
      expiresAt: Date.now() + 6e4,
    },
    user: { id: "u1", username: "admin", role: "admin" },
    isHydrated: true,
    setTokens: vi.fn(),
    setSession: vi.fn(),
    setUser: vi.fn(),
    clear: vi.fn(),
    hydrate: vi.fn(),
  };
  type S = typeof state;
  const useAuthStore = vi.fn((sel?: (s: S) => unknown) =>
    typeof sel === "function" ? sel(state) : state,
  ) as unknown as ((sel?: (s: S) => unknown) => unknown) & {
    getState: () => S;
    persist: { hasHydrated: () => boolean };
  };
  useAuthStore.getState = () => state;
  useAuthStore.persist = { hasHydrated: () => true };
  return { useAuthStore };
});

vi.mock("@/lib/toast", () => ({ toast: vi.fn() }));

import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { useUiStore } from "@/stores/uiStore";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{child}</MemoryRouter>
    </QueryClientProvider>
  );
}

// Find the grid <div> that contains a card with the given title.
function gridContainerFor(
  container: HTMLElement,
  cardTitle: string,
): HTMLElement | null {
  const headings = Array.from(container.querySelectorAll("*"));
  for (const el of headings) {
    if (el.textContent?.trim().startsWith(cardTitle)) {
      // Walk up to the nearest grid container.
      let node: HTMLElement | null = el as HTMLElement;
      while (node && node !== container) {
        if (node.className && /grid-cols/.test(node.className)) {
          return node;
        }
        node = node.parentElement;
      }
    }
  }
  return null;
}

beforeEach(() => {
  apiGet.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/dashboard/overview") {
      return {
        files_total: 0,
        files_by_severity: {},
        issues_open: 0,
        bytes_total: 0,
        recent_scan: null,
        recent_scan_status: null,
        scans_24h: 0,
        scans_24h_failed: 0,
        scans_24h_succeeded: 0,
      };
    }
    if (path.startsWith("/dashboard/series")) {
      return {
        days: 30,
        labels: [],
        issues_opened: [],
        files_scanned: [],
        scan_successes: [],
        scan_failures: [],
      };
    }
    if (path === "/dashboard/libraries") return [];
    if (path === "/dashboard/integrations") return [];
    if (path.startsWith("/dashboard/top-rules")) return [];
    if (path.startsWith("/dashboard/recent-scans")) return [];
    if (path.startsWith("/dashboard/recent-job-runs")) return [];
    if (path.startsWith("/dashboard/categories")) return [];
    if (path.startsWith("/rules/suggestions")) return { items: [], total: 0 };
    if (path === "/libraries") return [];
    if (path.startsWith("/scans/progress")) return [];
    return null;
  });
  // Reset to default (nothing hidden).
  useUiStore.setState({ dashboardHidden: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 6 — Granular hide layout", () => {
  it("uses xl:grid-cols-2 when both libraries and integrations are visible", () => {
    const { container } = render(wrap(<DashboardPage />));
    const grid = gridContainerFor(container, "Libraries");
    expect(grid).not.toBeNull();
    expect(grid!.className).toContain("xl:grid-cols-2");
    expect(grid!.className).not.toContain("xl:grid-cols-1");
  });

  it("collapses to xl:grid-cols-1 when EXACTLY ONE of the paired pair is hidden", () => {
    useUiStore.setState({ dashboardHidden: ["libraries"] });
    const { container } = render(wrap(<DashboardPage />));
    const grid = gridContainerFor(container, "Libraries");
    expect(grid).not.toBeNull();
    expect(grid!.className).toContain("xl:grid-cols-1");
    expect(grid!.className).not.toContain("xl:grid-cols-2");
  });

  it("stays xl:grid-cols-2 when BOTH paired cards are hidden (headers-only row)", () => {
    useUiStore.setState({
      dashboardHidden: ["libraries", "integrations"],
    });
    const { container } = render(wrap(<DashboardPage />));
    const grid = gridContainerFor(container, "Libraries");
    expect(grid).not.toBeNull();
    expect(grid!.className).toContain("xl:grid-cols-2");
  });

  it("applies the same logic to the recent-scans / recent-jobs row", () => {
    useUiStore.setState({ dashboardHidden: ["recent-jobs"] });
    const { container } = render(wrap(<DashboardPage />));
    const grid = gridContainerFor(container, "Recent scans");
    expect(grid).not.toBeNull();
    expect(grid!.className).toContain("xl:grid-cols-1");
  });
});
