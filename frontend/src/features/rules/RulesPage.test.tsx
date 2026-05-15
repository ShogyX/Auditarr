/**
 * Stage 24 — Rules page behavior tests.
 *
 * Pins the operational contracts of the rewritten Rules page:
 *
 *   - Custom / Suggestions tab strip switches the visible card
 *   - searching filters the rule list
 *   - clicking Duplicate POSTs to the right endpoint
 *   - Export action fetches the bundle
 *   - Import dialog opens and submits with the right body
 *
 * Mocks ``apiClient`` per-call so we can observe the GET / POST
 * traffic the page issues. The RuleDialog editor is exercised via
 * the existing rule-CRUD round-trip tests in
 * ``test_rules_api.py`` on the backend side; this file focuses on
 * the new Stage 24 page-level interactions.
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

// ── Fixtures ──────────────────────────────────────────────────
const RULE_A = {
  id: "r-aaa",
  name: "HEVC media",
  description: "Tag HEVC media files",
  enabled: true,
  priority: 100,
  last_evaluated_at: "2026-05-10T12:00:00Z",
  last_match_count: 42,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-10T12:00:00Z",
  definition: {
    match: { field: "video_codec", op: "eq", value: "hevc" },
    actions: [
      { type: "set_severity", severity: "info" },
      { type: "add_tag", tag: "hevc" },
    ],
  },
};

const RULE_B = {
  ...RULE_A,
  id: "r-bbb",
  name: "Bitrate ceiling",
  description: "Flag fat-bitrate remuxes",
  enabled: false,
  priority: 50,
  last_match_count: 0,
  definition: {
    match: { field: "bitrate_kbps", op: "gt", value: 50000 },
    actions: [{ type: "set_severity", severity: "warn" }],
  },
};

const SUGGESTION_A = {
  id: "s-aaa",
  name: "Suggested: HEVC re-encode",
  reason: "Found 12 HEVC files over 40 Mbps",
  heuristic: "bitrate_ceiling",
  confidence: 0.85,
  files_affected: 12,
  est_runtime_seconds: 7200,
  status: "pending",
  definition: RULE_A.definition,
  created_at: "2026-05-01T00:00:00Z",
  deployed_at: null,
  deployed_rule_id: null,
  dismissed_at: null,
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
    if (path === "/rules") return [RULE_A, RULE_B];
    if (path === "/rules/suggestions") return [SUGGESTION_A];
    if (path === "/libraries") return [];
    if (path === "/rules/vocabulary") {
      return {
        fields: [],
        ops: { string: ["eq"] },
        severities: ["ok", "info", "warn", "high", "error", "crit"],
        actions: [],
      };
    }
    if (path.startsWith("/rules/bundle/export")) {
      return {
        version: "1",
        exported_at: "2026-05-12T00:00:00Z",
        rules: [
          {
            name: "HEVC media",
            description: null,
            enabled: true,
            priority: 100,
            definition: RULE_A.definition,
          },
        ],
      };
    }
    return null;
  });
  apiPost.mockImplementation(async (path: string) => {
    if (path.endsWith("/duplicate")) {
      return { ...RULE_A, id: "r-aaa-copy", name: "HEVC media (copy)", enabled: false };
    }
    if (path === "/rules/bundle/import") {
      return {
        created: 1,
        renamed: 0,
        overwritten: 0,
        skipped: 0,
        errors: 0,
        outcomes: [
          {
            name: "HEVC media",
            final_name: "HEVC media",
            action: "created",
            rule_id: "new-id",
            error: null,
          },
        ],
      };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────

describe("RulesPage", () => {
  it("renders both rules in the table by default", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");
    expect(screen.getByText("Bitrate ceiling")).toBeInTheDocument();
  });

  it("tab strip switches between Custom and Suggestions", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    // The Custom tab is active by default — rules table visible.
    expect(screen.getByRole("tab", { name: /custom/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    // Switch to Suggestions.
    fireEvent.click(screen.getByRole("tab", { name: /suggestions/i }));

    // Suggestion card content should now appear.
    await screen.findByText(/HEVC re-encode/i);
    // Custom table headers should no longer be in the DOM.
    expect(
      screen.queryByRole("columnheader", { name: /priority/i }),
    ).not.toBeInTheDocument();
  });

  it("search filters the rules table", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    const search = screen.getByPlaceholderText(/search rules/i);
    fireEvent.change(search, { target: { value: "bitrate" } });

    await waitFor(() =>
      expect(screen.queryByText("HEVC media")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Bitrate ceiling")).toBeInTheDocument();
  });

  it("Duplicate row action POSTs to the right endpoint", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    // The duplicate button has an aria-label per rule — disambiguate
    // on the rule name.
    const dup = screen.getByRole("button", { name: /duplicate hevc media/i });
    fireEvent.click(dup);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/rules/r-aaa/duplicate",
        undefined,
      );
    });
  });

  it("Export button fetches the bundle", async () => {
    // Stub URL.createObjectURL so the download path doesn't blow up
    // in jsdom (it doesn't implement Blob URLs).
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:fake");
    URL.revokeObjectURL = vi.fn();

    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    fireEvent.click(screen.getByRole("button", { name: /export/i }));

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith("/rules/bundle/export");
    });

    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  it("Import button opens the import dialog", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    fireEvent.click(screen.getByRole("button", { name: /^import$/i }));

    const dialog = await screen.findByRole("dialog", { name: /import rules/i });
    expect(within(dialog).getByPlaceholderText(/version/i)).toBeInTheDocument();
  });

  it("Import dialog submits the bundle with the chosen strategy", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    fireEvent.click(screen.getByRole("button", { name: /^import$/i }));
    const dialog = await screen.findByRole("dialog", { name: /import rules/i });

    const textarea = within(dialog).getByPlaceholderText(/version/i);
    const bundle = {
      version: "1",
      exported_at: "2026-05-12T00:00:00Z",
      rules: [
        {
          name: "HEVC media",
          description: null,
          enabled: true,
          priority: 100,
          definition: RULE_A.definition,
        },
      ],
    };
    fireEvent.change(textarea, { target: { value: JSON.stringify(bundle) } });

    // Choose "Overwrite" strategy.
    fireEvent.click(within(dialog).getByRole("radio", { name: /overwrite/i }));

    fireEvent.click(
      within(dialog).getByRole("button", { name: /import rules/i }),
    );

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/rules/bundle/import",
        expect.objectContaining({
          bundle: expect.objectContaining({ version: "1" }),
          on_conflict: "overwrite",
        }),
      );
    });

    // After submit, the per-rule outcome list should appear.
    await within(dialog).findByText(/per-rule outcomes/i);
    expect(within(dialog).getByText("HEVC media")).toBeInTheDocument();
  });

  it("toggle switch on a row PATCHes the rule's enabled state", async () => {
    render(wrap(<RulesPage />));
    await screen.findByText("HEVC media");

    // Find the switch inside the disabled rule's row (Bitrate ceiling).
    const bitrateRow = screen.getByText("Bitrate ceiling").closest("tr");
    expect(bitrateRow).toBeTruthy();
    const toggle = within(bitrateRow as HTMLElement).getByRole("switch");
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(apiPatch).toHaveBeenCalledWith("/rules/r-bbb", {
        enabled: true,
      });
    });
  });

  it("derived severity column shows the highest-rank set_severity", async () => {
    render(wrap(<RulesPage />));
    // RULE_A has set_severity 'info'; RULE_B has 'warn'.
    await screen.findByText("HEVC media");
    const aRow = screen.getByText("HEVC media").closest("tr") as HTMLElement;
    const bRow = screen.getByText("Bitrate ceiling").closest("tr") as HTMLElement;
    expect(within(aRow).getByText("info")).toBeInTheDocument();
    expect(within(bRow).getByText("warn")).toBeInTheDocument();
  });
});
