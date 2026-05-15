/**
 * Stage 25 — Plugins page behavior tests.
 *
 * Pins the operational contracts of the new Plugins page:
 *
 *   - Installed / Gallery tabs switch the visible content
 *   - search filters the installed list
 *   - clicking Reload POSTs to /plugins/{id}/reload
 *   - lifecycle-errors panel renders when at least one plugin is
 *     errored or failed_to_load
 *   - status pills reflect the enriched ``status`` field
 *
 * Mocks ``apiClient`` per-call so we can observe traffic. The
 * settings dialog is exercised via its own component path; this
 * file focuses on page-level interactions.
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

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
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

import { PluginsPage } from "@/features/plugins/PluginsPage";

// ── Fixtures ──────────────────────────────────────────────────
const PLUGIN_LOADED = {
  id: "alpha",
  name: "Alpha plugin",
  version: "0.2.0",
  type: "generic",
  description: "Stage 25 fixture plugin",
  author: "ACME",
  status: "loaded" as const,
  last_error: null,
  has_settings: true,
  capabilities: ["alpha.cap"],
  routes: false,
};

const PLUGIN_ERRORED = {
  id: "broken",
  name: "Broken plugin",
  version: "0.1.0",
  type: "generic",
  description: "Throws on load",
  author: "test",
  status: "errored" as const,
  last_error: "on_load: intentional failure",
  has_settings: false,
  capabilities: [],
  routes: false,
};

const PLUGIN_FAILED = {
  id: "wontload",
  name: "Won't load",
  version: "0.0.1",
  type: "generic",
  description: "Module fails to import",
  author: "test",
  status: "failed_to_load" as const,
  last_error: "RuntimeError: bang",
  has_settings: false,
  capabilities: [],
  routes: false,
};

const GALLERY_RESPONSE = {
  ok: true,
  feed_url: "https://gallery.test/manifest.json",
  detail: null,
  plugins: [
    {
      id: "fingerprint",
      name: "Audio fingerprinting",
      description: "Detect duplicate tracks",
      author: null,
      version: "0.3.0",
      source_url: "https://github.com/example/fp",
      install_url: null,
      install_instructions: null,
      categories: ["analysis"],
      installed: false,
    },
  ],
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

  apiGet.mockImplementation(async (path: string) => {
    if (path === "/plugins") {
      return [PLUGIN_LOADED, PLUGIN_ERRORED, PLUGIN_FAILED];
    }
    if (path === "/plugins/gallery") return GALLERY_RESPONSE;
    return null;
  });
  apiPost.mockImplementation(async (path: string) => {
    if (path.endsWith("/reload")) {
      // After reload, assume it loaded cleanly.
      return { ...PLUGIN_LOADED, status: "loaded", last_error: null };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────

describe("PluginsPage", () => {
  it("renders every plugin in the installed table by default", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");
    // "Broken plugin" / "Won't load" also appear in the lifecycle
    // errors panel below — assert presence in the main grid.
    const grid = screen.getByRole("grid");
    expect(within(grid).getByText("Alpha plugin")).toBeInTheDocument();
    expect(within(grid).getByText("Broken plugin")).toBeInTheDocument();
    expect(within(grid).getByText("Won't load")).toBeInTheDocument();
  });

  it("status pills reflect the enriched status field", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    // The status column uses Pill text; multiple instances exist
    // (one per row + possibly in lifecycle errors panel). Just
    // assert each token appears at least once.
    expect(screen.getAllByText("loaded").length).toBeGreaterThan(0);
    expect(screen.getAllByText("errored").length).toBeGreaterThan(0);
    expect(screen.getAllByText("failed").length).toBeGreaterThan(0);
  });

  it("tab strip switches Installed / Gallery", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    fireEvent.click(screen.getByRole("tab", { name: /gallery/i }));

    await screen.findByText("Audio fingerprinting");
    // Installed rows no longer rendered while the gallery is shown.
    expect(screen.queryByText("Alpha plugin")).not.toBeInTheDocument();
  });

  it("search filters the installed plugins list", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    const search = screen.getByPlaceholderText(/search plugins/i);
    fireEvent.change(search, { target: { value: "broken" } });

    // After filtering, Alpha is gone from the grid (but the
    // lifecycle errors panel is unaffected — it always shows
    // every errored plugin).
    await waitFor(() => {
      const grid = screen.getByRole("grid");
      expect(within(grid).queryByText("Alpha plugin")).not.toBeInTheDocument();
    });
    const grid = screen.getByRole("grid");
    expect(within(grid).getByText("Broken plugin")).toBeInTheDocument();
  });

  it("Reload button POSTs to /plugins/{id}/reload", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    // Find the reload button inside the alpha row in the main grid.
    const grid = screen.getByRole("grid");
    const alphaRow = within(grid)
      .getByText("Alpha plugin")
      .closest("tr") as HTMLElement;
    const reloadBtn = within(alphaRow).getByRole("button", {
      name: /reload/i,
    });
    fireEvent.click(reloadBtn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/plugins/alpha/reload",
        undefined,
      );
    });
  });

  it("Configure button only renders for plugins with has_settings", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    const grid = screen.getByRole("grid");
    const alphaRow = within(grid)
      .getByText("Alpha plugin")
      .closest("tr") as HTMLElement;
    const brokenRow = within(grid)
      .getByText("Broken plugin")
      .closest("tr") as HTMLElement;

    expect(
      within(alphaRow).queryByRole("button", { name: /configure/i }),
    ).toBeInTheDocument();
    expect(
      within(brokenRow).queryByRole("button", { name: /configure/i }),
    ).not.toBeInTheDocument();
  });

  it("lifecycle errors panel renders when status is errored or failed_to_load", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    // The panel header carries the count.
    const heading = await screen.findByRole("heading", {
      name: /lifecycle errors/i,
    });
    expect(heading).toBeInTheDocument();

    // The error messages render inline.
    expect(
      screen.getByText(/on_load: intentional failure/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/RuntimeError: bang/i)).toBeInTheDocument();
  });

  it("lifecycle errors panel is absent when all plugins are loaded", async () => {
    // Override the GET to return only the loaded plugin.
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/plugins") return [PLUGIN_LOADED];
      if (path === "/plugins/gallery") return GALLERY_RESPONSE;
      return null;
    });

    render(wrap(<PluginsPage />));
    await screen.findByText("Alpha plugin");

    expect(
      screen.queryByRole("heading", { name: /lifecycle errors/i }),
    ).not.toBeInTheDocument();
  });

  it("empty state renders when no plugins are installed", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/plugins") return [];
      if (path === "/plugins/gallery") return GALLERY_RESPONSE;
      return null;
    });

    render(wrap(<PluginsPage />));
    await screen.findByText(/no plugins installed/i);
  });
});
