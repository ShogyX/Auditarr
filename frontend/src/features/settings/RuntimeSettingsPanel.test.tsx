/**
 * Stage 22 — runtime-settings panel behavior.
 *
 * Beyond the smoke test (page mounts without throwing), these tests
 * exercise the panel's core operational contracts:
 *
 *   - schema-driven render (categories + per-field cards)
 *   - dirty edit → save bar appears with the right counts
 *   - clicking Apply opens the confirm diff dialog
 *   - the dialog surfaces per-field warnings only when relevant
 *   - applying calls the right endpoint (PUT vs DELETE for "going to
 *     default") on each dirty field
 *
 * Mocks ``apiClient`` per-call so we can both serve realistic
 * describe + values data AND observe the PUT/DELETE that the panel
 * issues on Apply.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";

// ── Mock apiClient with per-test access to the call log ────────
const apiGet = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    put: (path: string, body?: unknown) => apiPut(path, body),
    delete: (path: string) => apiDelete(path),
    post: vi.fn(async () => null),
    patch: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: { accessToken: "x", refreshToken: "x", expiresAt: Date.now() + 6e4 },
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

// Toast helper writes to a DOM node that doesn't exist in jsdom by
// default; stub it so the mutation-success branch doesn't throw.
vi.mock("@/lib/toast", () => ({ toast: vi.fn() }));

import { RuntimeSettingsPanel } from "@/features/settings/RuntimeSettingsPanel";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return <QueryClientProvider client={qc}>{child}</QueryClientProvider>;
}

const describePayload = {
  fields: [
    {
      key: "log_level",
      label: "Log level",
      description: "Minimum log severity emitted by the API and worker.",
      category: "logging",
      type: "string",
      default: "info",
      constraints: { pattern: "^(debug|info|warning|error|critical)$" },
      impact: "immediate",
      requires_warning: "DEBUG produces a lot of journal noise.",
    },
    {
      key: "scanner_worker_concurrency",
      label: "Scanner worker concurrency",
      description: "Files the scanner processes in parallel.",
      category: "scanner",
      type: "integer",
      default: 4,
      constraints: { ge: 1, le: 32 },
      impact: "next_tick",
      requires_warning: null,
    },
  ],
};

const valuesPayload = {
  // log_level is at env default.
  log_level: { value: "info", is_override: false, env_default: "info" },
  // scanner_worker_concurrency has been overridden to 8.
  scanner_worker_concurrency: { value: 8, is_override: true, env_default: 4 },
};

beforeEach(() => {
  apiGet.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/system/runtime-settings/describe") return describePayload;
    if (path === "/system/runtime-settings") return valuesPayload;
    return null;
  });
  apiPut.mockResolvedValue({ key: "stub", value: null, is_override: true });
  apiDelete.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RuntimeSettingsPanel", () => {
  it("renders schema-driven categories and field cards", async () => {
    render(wrap(<RuntimeSettingsPanel />));

    await screen.findByText("Log level");
    // Default category is the first one — logging — so log_level is visible
    // and the scanner field is not (it lives under a different rail entry).
    expect(screen.getByText("Logging")).toBeInTheDocument();
    expect(screen.getByText("Scanner")).toBeInTheDocument();
    expect(screen.getByText("log_level")).toBeInTheDocument();

    // The overridden field renders an "overridden" pill in its head.
    // Switch to the scanner category to see it.
    fireEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await screen.findByText("scanner_worker_concurrency");
    expect(screen.getByText(/overridden/i)).toBeInTheDocument();
  });

  it("reveals the save bar and warning only after a dirty edit", async () => {
    render(wrap(<RuntimeSettingsPanel />));
    await screen.findByText("Log level");

    // No save bar before any edit.
    expect(screen.queryByText(/changes? pending/i)).not.toBeInTheDocument();
    // Warning text is also hidden at rest — the prototype's design
    // intent is that "this is dangerous" only appears when you're
    // about to do the dangerous thing.
    expect(screen.queryByText(/journal noise/i)).not.toBeInTheDocument();

    // Switch log_level to debug — should make the field dirty AND show
    // the warning that the schema attaches to this key.
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "debug" } });

    await screen.findByText(/1 change pending/i);
    expect(screen.getByText(/journal noise/i)).toBeInTheDocument();
    expect(screen.getByText(/1 immediate/i)).toBeInTheDocument();
  });

  it("applies dirty edits via PUT and DELETE on confirm", async () => {
    render(wrap(<RuntimeSettingsPanel />));
    await screen.findByText("Log level");

    // Edit 1: change log_level (currently at default) → debug.
    // This should PUT.
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "debug" } });

    // Edit 2: switch to scanner category, restore the override to default.
    // This should DELETE (going-to-default detection).
    fireEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await screen.findByText("scanner_worker_concurrency");
    fireEvent.click(screen.getByRole("button", { name: /restore default/i }));

    await screen.findByText(/2 changes? pending/i);

    // Open the confirm dialog.
    fireEvent.click(
      screen.getByRole("button", { name: /apply 2 changes/i }),
    );
    const dialog = await screen.findByRole("dialog");

    // Diff table shows both rows. Scope to the dialog because the
    // field-card labels remain in the DOM behind the modal backdrop.
    expect(within(dialog).getByText("Log level")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Scanner worker concurrency"),
    ).toBeInTheDocument();
    // The going-to-default row carries the "clear" pill rather than
    // "next tick" — confirming the DELETE path is taken.
    expect(within(dialog).getByText("clear")).toBeInTheDocument();

    // Confirm.
    fireEvent.click(
      within(dialog).getByRole("button", { name: /apply changes/i }),
    );

    await waitFor(() => {
      expect(apiPut).toHaveBeenCalledWith(
        "/system/runtime-settings/log_level",
        { value: "debug" },
      );
      expect(apiDelete).toHaveBeenCalledWith(
        "/system/runtime-settings/scanner_worker_concurrency",
      );
    });
  });

  it("falls back to admin-required state when describe returns 403", async () => {
    // The hook's forbidden-detector is structural (anything with
    // ``status === 403``), so we just throw a duck-typed error here.
    // Going through the real ApiError class isn't necessary and would
    // require coordinating with the module mock's constructor shape.
    apiGet.mockImplementation(async () => {
      throw Object.assign(new Error("Admin only"), {
        status: 403,
        code: "forbidden",
      });
    });
    render(wrap(<RuntimeSettingsPanel />));
    await screen.findByText(/admin access required/i);
  });
});
