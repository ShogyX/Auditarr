/**
 * Stage 2 — Rules page / Automation tab regression tests.
 *
 * Pins the contracts established in Stage 2 of the consolidated
 * audit fix plan:
 *
 *   1. When the Automation tab is active, all three sub-cards
 *      (Schedules / Recent runs / Optimization queue) render
 *      unconditionally — even while ``/automation/jobs`` is still
 *      loading. Only the "New schedule" button is disabled during
 *      the load.
 *   2. Deep-linking to ``/rules?tab=automation`` lands on the
 *      Automation tab on first render (the redirect from the
 *      legacy /automation route relies on this).
 *   3. The page header's "New schedule" button is visible on the
 *      Automation tab and the rule-specific actions ("New rule",
 *      "Evaluate") are not.
 *   4. Clicking the header's New schedule sets ``?new=schedule``
 *      and the dialog (controlled by URL state) opens on the body.
 *
 * Mocks ``apiClient`` per-call so we can stage a never-resolving
 * ``/automation/jobs`` response to assert the cards stay visible
 * during the kinds-load.
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
function wrap(child: ReactNode, initialUrl = "/rules"): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialUrl]}>{child}</MemoryRouter>
    </QueryClientProvider>
  );
}

function defaultGetImpl(path: string): Promise<unknown> {
  // Sub-card queries — resolve to empty lists so the cards render
  // their "no data yet" states instead of error states.
  if (path === "/automation/schedules") return Promise.resolve([]);
  if (path.startsWith("/automation/runs")) return Promise.resolve([]);
  if (path.startsWith("/automation/optimization-queue"))
    return Promise.resolve([]);
  if (path === "/automation/jobs")
    return Promise.resolve([
      {
        key: "scan_library",
        label: "Scan library",
        description: "Walk a library",
        args_schema: { type: "object", properties: {} },
        required_args: [],
        timeout_seconds: 600,
      },
    ]);
  // Rules-page queries — keep them empty so the page mounts.
  if (path === "/rules") return Promise.resolve([]);
  if (path === "/rules?is_builtin=true") return Promise.resolve([]);
  if (path === "/rules/suggestions") return Promise.resolve([]);
  if (path === "/libraries") return Promise.resolve([]);
  if (path === "/rules/vocabulary")
    return Promise.resolve({
      fields: [],
      operators: [],
      severity_levels: [],
      actions: [],
    });
  return Promise.resolve(null);
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiDelete.mockReset();
  apiGet.mockImplementation(defaultGetImpl);
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1: deep-link landing on Automation tab ────────────────────
describe("Stage 2 — deep-link", () => {
  it("?tab=automation lands on the Automation tab on first render", async () => {
    render(wrap(<RulesPage />, "/rules?tab=automation"));

    // The Automation tab must be aria-selected.
    const automationTab = await screen.findByRole("tab", {
      name: /automation/i,
    });
    await waitFor(() =>
      expect(automationTab).toHaveAttribute("aria-selected", "true"),
    );

    // And the Custom tab must NOT be selected (sanity).
    expect(
      screen.getByRole("tab", { name: /custom/i }),
    ).toHaveAttribute("aria-selected", "false");
  });
});

// ── 2: sub-cards render while jobKinds is loading ─────────────
describe("Stage 2 — automation sub-cards", () => {
  it("renders Schedules / Recent runs / Optimization queue while jobKinds is still loading", async () => {
    // Override the GET mock so /automation/jobs hangs forever; the
    // three sub-card queries still resolve. We assert that the
    // sub-card titles are visible regardless.
    apiGet.mockImplementation((path: string) => {
      if (path === "/automation/jobs") {
        return new Promise(() => {
          /* never resolves — simulates pending load */
        });
      }
      return defaultGetImpl(path);
    });

    render(wrap(<RulesPage />, "/rules?tab=automation"));

    // All three sub-card titles must appear.
    await waitFor(() =>
      expect(screen.getByText("Schedules")).toBeInTheDocument(),
    );
    expect(screen.getByText("Recent runs")).toBeInTheDocument();
    expect(screen.getByText("Optimization queue")).toBeInTheDocument();

    // The header's "New schedule" button is visible but disabled
    // while jobKinds is loading (clicking it would open a dialog
    // that can't populate its job-kind picker).
    const newScheduleBtn = screen.getByRole("button", {
      name: /new schedule/i,
    });
    expect(newScheduleBtn).toBeDisabled();
  });

  it("renders the three sub-cards once jobKinds resolves, and the New schedule button enables", async () => {
    render(wrap(<RulesPage />, "/rules?tab=automation"));

    // Titles + button name are the visible artefacts.
    await waitFor(() =>
      expect(screen.getByText("Schedules")).toBeInTheDocument(),
    );
    expect(screen.getByText("Recent runs")).toBeInTheDocument();
    expect(screen.getByText("Optimization queue")).toBeInTheDocument();

    const newScheduleBtn = await screen.findByRole("button", {
      name: /new schedule/i,
    });
    await waitFor(() => expect(newScheduleBtn).not.toBeDisabled());
  });
});

// ── 3: header CTAs split by tab ───────────────────────────────
describe("Stage 2 — header CTAs", () => {
  it("Automation tab shows New schedule but not New rule / Evaluate", async () => {
    render(wrap(<RulesPage />, "/rules?tab=automation"));

    await screen.findByRole("tab", { name: /automation/i });

    // New schedule is in the page header.
    expect(
      screen.getByRole("button", { name: /new schedule/i }),
    ).toBeInTheDocument();
    // New rule and Evaluate are rule-specific and must be absent.
    expect(
      screen.queryByRole("button", { name: /new rule/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^evaluate$/i }),
    ).not.toBeInTheDocument();
  });

  it("Custom tab shows New rule but not New schedule", async () => {
    render(wrap(<RulesPage />, "/rules"));

    // Wait for the page to settle on Custom.
    await waitFor(() =>
      expect(
        screen.getByRole("tab", { name: /custom/i }),
      ).toHaveAttribute("aria-selected", "true"),
    );

    expect(
      screen.getByRole("button", { name: /new rule/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /new schedule/i }),
    ).not.toBeInTheDocument();
  });
});

// ── 4: header New schedule click opens the dialog (URL-driven) ─
describe("Stage 2 — header New schedule wiring", () => {
  it("clicking the header's New schedule opens the dialog (controlled by ?new=schedule)", async () => {
    render(wrap(<RulesPage />, "/rules?tab=automation"));

    const btn = await screen.findByRole("button", {
      name: /new schedule/i,
    });
    // Wait for jobKinds to finish so the button becomes enabled.
    await waitFor(() => expect(btn).not.toBeDisabled());

    fireEvent.click(btn);

    // Radix Dialog renders the open dialog with role="dialog".
    // We look for the role rather than a heading because the
    // ``ModalHead`` component renders the title twice — once as a
    // visible <h2> and once as an sr-only <h2> for screen readers,
    // and "Found multiple elements with the role 'heading' and
    // name /new schedule/" is the wrong failure mode for this test.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
  });

  it("?new=schedule on the URL opens the dialog directly on first render", async () => {
    render(
      wrap(<RulesPage />, "/rules?tab=automation&new=schedule"),
    );

    // jobKinds must have resolved before the dialog renders (the
    // dialog needs the kinds list); waitFor the dialog appearance.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
  });
});
