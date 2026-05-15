/**
 * Stage 30 — Routed full-screen rule editor tests.
 *
 * Pins:
 *
 *   - /rules/new renders the editor in create mode (no rule fetch).
 *   - /rules/:ruleId/edit renders the editor in edit mode and
 *     fetches the rule by id.
 *   - Save in edit mode PATCHes the rule.
 *   - Create in new mode POSTs a fresh rule.
 *   - "Back" navigates to /rules without saving.
 *   - Built-in rules render the read-only banner + Duplicate
 *     primary CTA; Save is not present.
 *   - 404 on the rule fetch shows the "Rule not found" empty
 *     state with a back link.
 *
 * The RulesPage list-side wiring (clicking a row navigates here)
 * is covered separately by the Stage 24 / 29 page tests that
 * still pass with the navigate change.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
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
import { MemoryRouter, Route, Routes } from "react-router-dom";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
    patch: (path: string, body?: unknown) => apiPatch(path, body),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
    constructor(msg: string, status = 500) {
      super(msg);
      this.status = status;
    }
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

import { RuleEditorPage } from "@/features/rules/RuleEditorPage";

// Stub a "rules list" landing so the back-navigate has somewhere
// to go. The body just renders a marker string we can assert on.
function RulesListStub() {
  return <div data-testid="rules-list-landing">rules list</div>;
}

const VOCAB = {
  fields: [],
  ops: { string: ["eq"] },
  severities: ["ok", "info", "warn", "high", "error", "crit"],
  actions: [],
};

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
  ...CUSTOM_RULE,
  id: "r-builtin",
  name: "Orphaned files",
  description: "Flag files the scanner can no longer find on disk.",
  is_builtin: true,
};

function renderAt(initialPath: string): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/rules" element={<RulesListStub />} />
          <Route path="/rules/new" element={<RuleEditorPage />} />
          <Route path="/rules/:ruleId/edit" element={<RuleEditorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();

  apiGet.mockImplementation(async (path: string) => {
    if (path === "/rules/vocabulary") return VOCAB;
    if (path === "/rules/r-cust") return CUSTOM_RULE;
    if (path === "/rules/r-builtin") return BUILTIN_RULE;
    if (path === "/rules/r-missing") {
      throw new Error("Rule not found");
    }
    if (path.startsWith("/media")) {
      return { items: [], total: 0, offset: 0, limit: 25 };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ────────────────────────────────────────────────────

describe("Stage 30 — Routed rule editor", () => {
  it("/rules/new renders the editor in create mode (no rule fetch)", async () => {
    render(renderAt("/rules/new"));

    // The title is "New rule" in create mode.
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /new rule/i }))
        .toBeInTheDocument();
    });

    // No GET /rules/* should have fired for fetching a specific
    // rule (the vocab + libraries queries are fine).
    const ruleFetches = apiGet.mock.calls.filter(([p]) => {
      const s = String(p);
      return (
        s.startsWith("/rules/") &&
        s !== "/rules/vocabulary" &&
        !s.startsWith("/rules/suggestions")
      );
    });
    expect(ruleFetches).toHaveLength(0);
  });

  it("/rules/:ruleId/edit fetches the rule by id and pre-fills the name", async () => {
    render(renderAt("/rules/r-cust/edit"));

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith("/rules/r-cust");
    });

    // Pre-filled name.
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/flag big hevc files/i);
      expect((input as HTMLInputElement).value).toBe("Custom rule");
    });
  });

  it("Save on a custom rule PATCHes with the right body", async () => {
    apiPatch.mockResolvedValue({ ...CUSTOM_RULE, name: "Renamed" });

    render(renderAt("/rules/r-cust/edit"));

    // Wait for pre-fill.
    const nameInput = (await screen.findByPlaceholderText(
      /flag big hevc files/i,
    )) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Renamed" } });

    const save = screen.getByRole("button", { name: /^save$/i });
    fireEvent.click(save);

    await waitFor(() => {
      expect(apiPatch).toHaveBeenCalledWith(
        "/rules/r-cust",
        expect.objectContaining({ name: "Renamed" }),
      );
    });
  });

  it("Create on /rules/new POSTs a fresh rule", async () => {
    apiPost.mockResolvedValue({ ...CUSTOM_RULE, id: "r-new" });

    render(renderAt("/rules/new"));

    const nameInput = (await screen.findByPlaceholderText(
      /flag big hevc files/i,
    )) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Fresh rule" } });

    const create = screen.getByRole("button", { name: /^create$/i });
    fireEvent.click(create);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/rules",
        expect.objectContaining({ name: "Fresh rule" }),
      );
    });
  });

  it("Back navigates to /rules without saving", async () => {
    render(renderAt("/rules/r-cust/edit"));

    // Wait for body to render past the loading state (the name
    // input only renders in the body, not in the loading branch).
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/flag big hevc files/i);
      expect((input as HTMLInputElement).value).toBe("Custom rule");
    });

    const back = screen.getByRole("button", { name: /^back$/i });
    fireEvent.click(back);

    await waitFor(() => {
      expect(screen.getByTestId("rules-list-landing")).toBeInTheDocument();
    });
    // And no PATCH fired.
    expect(apiPatch).not.toHaveBeenCalled();
  });

  it("Escape navigates to /rules without saving", async () => {
    render(renderAt("/rules/r-cust/edit"));

    await waitFor(() => {
      const input = screen.getByPlaceholderText(/flag big hevc files/i);
      expect((input as HTMLInputElement).value).toBe("Custom rule");
    });

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => {
      expect(screen.getByTestId("rules-list-landing")).toBeInTheDocument();
    });
    expect(apiPatch).not.toHaveBeenCalled();
  });

  it("Built-in rule renders the read-only banner and hides Save", async () => {
    render(renderAt("/rules/r-builtin/edit"));

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith("/rules/r-builtin");
    });

    // The banner mentions "built-in".
    await waitFor(() => {
      expect(screen.getByText(/this is a built-in rule/i))
        .toBeInTheDocument();
    });

    // Save button must NOT be present (builtins can't save).
    expect(
      screen.queryByRole("button", { name: /^save$/i }),
    ).not.toBeInTheDocument();
  });

  it("Built-in rule's Name input is disabled", async () => {
    render(renderAt("/rules/r-builtin/edit"));

    const nameInput = (await screen.findByPlaceholderText(
      /flag big hevc files/i,
    )) as HTMLInputElement;
    expect(nameInput).toBeDisabled();
  });

  it("Built-in rule's Duplicate-as-custom CTA POSTs the duplicate endpoint", async () => {
    apiPost.mockResolvedValue({
      ...BUILTIN_RULE,
      id: "r-newcopy",
      name: "Orphaned files (copy)",
      is_builtin: false,
      enabled: false,
    });

    render(renderAt("/rules/r-builtin/edit"));

    const dupBtn = await screen.findByRole("button", {
      name: /duplicate as custom rule/i,
    });
    fireEvent.click(dupBtn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        `/rules/${BUILTIN_RULE.id}/duplicate`,
        undefined,
      );
    });
  });

  it("Rule-not-found renders the empty state with a back link", async () => {
    render(renderAt("/rules/r-missing/edit"));

    await waitFor(() => {
      // "Rule not found" appears twice (title + description
      // since the thrown error message happens to match the
      // EmptyState title). Either is fine — the assertion is
      // that the empty-state surface is visible.
      const matches = screen.getAllByText(/rule not found/i);
      expect(matches.length).toBeGreaterThan(0);
    });
    expect(
      screen.getByRole("button", { name: /back to rules/i }),
    ).toBeInTheDocument();
  });
});
