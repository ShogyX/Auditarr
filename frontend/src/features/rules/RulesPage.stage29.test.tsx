/**
 * Stage 29 — Built-in rules tab + row behavior tests.
 *
 * Pins:
 *
 *   - Built-in tab appears in the tab strip with a correct count.
 *   - Switching to the Built-in tab queries
 *     ``/rules?is_builtin=true``.
 *   - Built-in rows render a "Built-in" badge.
 *   - Built-in rows' Delete button is disabled with a tooltip
 *     pointing operators at the "disable instead" workflow.
 *   - Built-in rows' Duplicate button is enabled and primary.
 *   - The Custom tab count excludes built-in rules.
 *
 * The full edit/delete protection contract is covered by the
 * backend integration tests in ``test_rules_builtin_stage29.py``;
 * these frontend tests verify the UI surfaces the protection
 * correctly rather than re-asserting the same contract twice.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
    put: vi.fn(async () => null),
    delete: (path: string) => apiDelete(path),
    patch: (path: string, body?: unknown) => apiPatch(path, body),
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
    user: { id: "u1", username: "tester", role: "admin" },
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

import { RulesPage } from "@/features/rules/RulesPage";

// ── Fixtures ─────────────────────────────────────────────────
const CUSTOM_RULE = {
  id: "r-cust",
  name: "Custom rule",
  description: "An operator-authored rule",
  enabled: true,
  priority: 100,
  is_builtin: false,
  last_evaluated_at: null,
  last_match_count: 0,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
  definition: {
    match: { field: "video_codec", op: "eq", value: "h264" },
    actions: [{ type: "add_tag", tag: "h264" }],
  },
};

const BUILTIN_RULE = {
  id: "r-builtin",
  name: "Orphaned files",
  description: "Flag files the scanner can no longer find on disk.",
  enabled: true,
  priority: 10,
  is_builtin: true,
  last_evaluated_at: null,
  last_match_count: 5,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
  definition: {
    match: { all: [{ field: "is_orphaned", op: "eq", value: true }] },
    actions: [
      { type: "set_severity", severity: "warn" },
      { type: "add_tag", tag: "orphaned" },
    ],
  },
};

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

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiDelete.mockReset();

  apiGet.mockImplementation(async (path: string) => {
    // The union list (no filter) returns both custom + builtin.
    if (path === "/rules") return [CUSTOM_RULE, BUILTIN_RULE];
    // The builtin-only filter:
    if (path === "/rules?is_builtin=true") return [BUILTIN_RULE];
    if (path === "/rules/suggestions") return [];
    if (path === "/libraries") return [];
    if (path === "/rules/vocabulary") {
      return {
        fields: [],
        operators: [],
        severity_levels: [],
        actions: [],
      };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ────────────────────────────────────────────────────

describe("Stage 29 — Built-in rules tab", () => {
  it("renders a Built-in tab with the correct count", async () => {
    render(wrap(<RulesPage />));

    // Wait for both queries to resolve.
    await waitFor(() => {
      const tab = screen.getByRole("tab", { name: /built-in/i });
      // The count badge is part of the same button.
      expect(tab.textContent).toMatch(/built-in.*1/i);
    });
  });

  it("Custom tab count excludes built-in rules", async () => {
    render(wrap(<RulesPage />));

    await waitFor(() => {
      const tab = screen.getByRole("tab", { name: /custom/i });
      // Only the one custom rule should be counted; BUILTIN_RULE
      // is in the union response but the Custom tab filters it
      // out client-side.
      expect(tab.textContent).toMatch(/custom.*1/i);
    });
  });

  it("switching to Built-in queries /rules?is_builtin=true", async () => {
    render(wrap(<RulesPage />));

    await waitFor(() => screen.getByRole("tab", { name: /built-in/i }));

    // Sanity: the union query fired already (Custom tab default).
    expect(apiGet).toHaveBeenCalledWith("/rules");

    // Click the Built-in tab — the filter query should fire.
    const tab = screen.getByRole("tab", { name: /built-in/i });
    fireEvent.click(tab);

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith("/rules?is_builtin=true");
    });
  });

  it("renders a Built-in badge on built-in rows", async () => {
    render(wrap(<RulesPage />));
    const tab = await screen.findByRole("tab", { name: /built-in/i });
    fireEvent.click(tab);

    await waitFor(() => {
      expect(screen.getByText("Orphaned files")).toBeInTheDocument();
    });

    const row = screen.getByText("Orphaned files").closest("tr");
    expect(row).toBeTruthy();
    // The badge text. We search inside the row to avoid matching
    // the tab strip label.
    expect(within(row as HTMLElement).getByText(/built-in/i))
      .toBeInTheDocument();
  });

  it("Delete button on a built-in row is disabled with helpful tooltip", async () => {
    render(wrap(<RulesPage />));
    const tab = await screen.findByRole("tab", { name: /built-in/i });
    fireEvent.click(tab);

    await waitFor(() => screen.getByText("Orphaned files"));

    const row = screen.getByText("Orphaned files").closest("tr");
    const deleteBtn = within(row as HTMLElement).getByRole("button", {
      name: /delete orphaned files/i,
    });
    expect(deleteBtn).toBeDisabled();
    expect(deleteBtn.getAttribute("title")).toMatch(/disable instead/i);
  });

  it("Duplicate button on a built-in row is enabled with 'as custom' framing", async () => {
    render(wrap(<RulesPage />));
    const tab = await screen.findByRole("tab", { name: /built-in/i });
    fireEvent.click(tab);

    await waitFor(() => screen.getByText("Orphaned files"));

    const row = screen.getByText("Orphaned files").closest("tr");
    const dupBtn = within(row as HTMLElement).getByRole("button", {
      name: /duplicate orphaned files as a custom rule/i,
    });
    expect(dupBtn).not.toBeDisabled();
    expect(dupBtn.getAttribute("title")).toMatch(/duplicate as a custom rule/i);
  });

  it("Duplicate on a built-in POSTs the duplicate endpoint", async () => {
    apiPost.mockImplementation(async (path: string) => {
      if (path === `/rules/${BUILTIN_RULE.id}/duplicate`) {
        return {
          ...BUILTIN_RULE,
          id: "r-newcopy",
          name: "Orphaned files (copy)",
          is_builtin: false,
          enabled: false,
        };
      }
      return null;
    });

    render(wrap(<RulesPage />));
    const tab = await screen.findByRole("tab", { name: /built-in/i });
    fireEvent.click(tab);

    await waitFor(() => screen.getByText("Orphaned files"));
    const row = screen.getByText("Orphaned files").closest("tr");
    const dupBtn = within(row as HTMLElement).getByRole("button", {
      name: /duplicate orphaned files as a custom rule/i,
    });
    fireEvent.click(dupBtn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        `/rules/${BUILTIN_RULE.id}/duplicate`,
        undefined,
      );
    });
  });

  it("toggling enabled on a built-in row PATCHes only the enabled field", async () => {
    apiPatch.mockImplementation(async () => ({
      ...BUILTIN_RULE,
      enabled: false,
    }));

    render(wrap(<RulesPage />));
    const tab = await screen.findByRole("tab", { name: /built-in/i });
    fireEvent.click(tab);

    await waitFor(() => screen.getByText("Orphaned files"));

    const row = screen.getByText("Orphaned files").closest("tr");
    const toggle = within(row as HTMLElement).getByRole("switch");
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(apiPatch).toHaveBeenCalledWith(
        `/rules/${BUILTIN_RULE.id}`,
        // The hook posts only the patch fields the operator
        // changed — enabled in this case.
        { enabled: false },
      );
    });
  });
});
