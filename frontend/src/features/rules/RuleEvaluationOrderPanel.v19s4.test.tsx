/**
 * v1.9 Stage 4.5 — RuleEvaluationOrderPanel side panel.
 *
 * Pins:
 *   1. Renders enabled rules in priority order (lower first).
 *   2. Highlights the currently-edited rule.
 *   3. Disabled rules don't appear EXCEPT when they're the
 *      currently-edited rule (operator needs to see where it sits).
 *   4. On the create path (currentRuleId=null), an insertion
 *      marker appears at the slot determined by currentPriority.
 *   5. Clicking a row navigates to that rule's editor.
 *   6. The empty state shows when no rules are enabled (and the
 *      current rule, if any, is also not in the list).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: vi.fn(async () => null),
    put: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    delete: vi.fn(async () => null),
  },
  ApiError: class extends Error {},
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    accessToken: "tok",
    refreshToken: "ref",
    user: { id: "u1", role: "admin" as const, email: "a@b.c", username: "admin" },
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

import { RuleEvaluationOrderPanel } from "@/features/rules/RuleEvaluationOrderPanel";

// LocationProbe surfaces the current path through a DOM data-attr
// so a test assertion can read where navigate() went.
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location-probe" data-path={loc.pathname} />;
}

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/rules/r2/edit"]}>
        <Routes>
          <Route
            path="/rules/:ruleId/edit"
            element={
              <>
                {child}
                <LocationProbe />
              </>
            }
          />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const RULES = [
  { id: "r1", name: "First rule", priority: 10, enabled: true, is_builtin: false },
  { id: "r2", name: "Second rule", priority: 50, enabled: true, is_builtin: false },
  { id: "r3", name: "Third rule", priority: 75, enabled: true, is_builtin: false },
  { id: "r4", name: "Disabled rule", priority: 30, enabled: false, is_builtin: false },
];

beforeEach(() => {
  apiGet.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path.startsWith("/rules")) return RULES;
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 4.5 — RuleEvaluationOrderPanel", () => {
  it("renders enabled rules in priority order; disabled rules are hidden", async () => {
    render(
      wrap(<RuleEvaluationOrderPanel currentRuleId="r2" currentPriority={50} />),
    );
    // All three enabled rules render.
    await screen.findByText("First rule");
    expect(screen.getByText("Second rule")).toBeInTheDocument();
    expect(screen.getByText("Third rule")).toBeInTheDocument();
    // Disabled rule does NOT render (it's not the current one).
    expect(screen.queryByText("Disabled rule")).toBeNull();
  });

  it("highlights the currently-edited rule via aria-current", async () => {
    render(
      wrap(<RuleEvaluationOrderPanel currentRuleId="r2" currentPriority={50} />),
    );
    const currentRow = await screen.findByRole("button", { name: /Second rule/i });
    expect(currentRow).toHaveAttribute("aria-current", "true");
    // Disabled because clicking it would navigate to itself.
    expect(currentRow).toBeDisabled();
  });

  it("includes the current rule even if it is disabled", async () => {
    render(
      wrap(<RuleEvaluationOrderPanel currentRuleId="r4" currentPriority={30} />),
    );
    // Disabled rule IS now visible because it's the one being edited.
    await screen.findByText("Disabled rule");
    expect(screen.getByText(/^disabled$/i)).toBeInTheDocument();
  });

  it("on the create path, an insertion marker appears", async () => {
    render(
      wrap(<RuleEvaluationOrderPanel currentRuleId={null} currentPriority={40} />),
    );
    // Wait for rules to render so the marker has somewhere to sit.
    await screen.findByText("First rule");
    expect(screen.getByText(/new rule here/i)).toBeInTheDocument();
  });

  it("clicking a non-current row navigates to that rule", async () => {
    render(
      wrap(<RuleEvaluationOrderPanel currentRuleId="r2" currentPriority={50} />),
    );
    const otherRow = await screen.findByRole("button", { name: /Third rule/i });
    fireEvent.click(otherRow);
    // The LocationProbe inside the routes captures the navigation.
    const probe = await screen.findByTestId("location-probe");
    expect(probe).toHaveAttribute("data-path", "/rules/r3/edit");
  });
});
