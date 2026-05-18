/**
 * v1.9 Stage 2.1 — PathMappingsPanel location.
 *
 * Pins the move:
 *   1. PathMappingsPanel renders on /integrations (IntegrationsPage).
 *   2. PathMappingsPanel does NOT render on /settings (SettingsPage),
 *      even in the System tab where it might be tempting to put it.
 *   3. The workspace summary card on /settings links to /integrations,
 *      not to an internal Settings sub-tab.
 *
 * The panel itself reads from /system/path-mappings. We mock the API
 * to return a single integration with no mappings so the panel
 * renders its empty-state heading and we can locate it by accessible
 * text.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
    tokens: { accessToken: "x", refreshToken: "x", expiresAt: Date.now() + 6e4 },
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

import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { SettingsPage } from "@/features/settings/SettingsPage";

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
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/libraries") return [];
    if (path === "/integrations") return [];
    if (path === "/integrations/kinds") return [];
    if (path.startsWith("/system/runtime-settings/describe")) {
      return { fields: [], categories: [] };
    }
    if (path.startsWith("/system/runtime-settings/values")) return {};
    if (path.startsWith("/system/runtime-settings/secrets")) return [];
    if (path.startsWith("/system/path-mappings/suggestions")) {
      return {
        library_roots: [],
        integration_paths: [],
        global_paths: [],
      };
    }
    if (path.startsWith("/system/path-mappings/global")) {
      return [];
    }
    if (path.startsWith("/system/path-mappings")) {
      return { integrations: [] };
    }
    if (path.startsWith("/system/config")) return { sections: [] };
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 2.1 — PathMappingsPanel moved to /integrations", () => {
  it("IntegrationsPage hosts the Path-mappings editor", () => {
    render(wrap(<IntegrationsPage />));
    // The panel renders a "Path mappings" heading at the top.
    // Multiple matches are acceptable (cards may share verbiage);
    // existence is what we assert.
    const headings = screen.getAllByText(/path mappings/i);
    expect(headings.length).toBeGreaterThan(0);
  });

  it("SettingsPage does NOT host the full Path-mappings editor", () => {
    // The Workspace tab has a SUMMARY card with the verbiage
    // "Path mappings" — that's expected and still wanted. What we
    // do NOT want is the heavy editor rendering on /settings.
    // The editor is identifiable by its "Global path mappings"
    // section heading (the per-integration accordion + the global
    // layer); absence of that heading anywhere on /settings is
    // proof the panel didn't follow into Settings.
    render(wrap(<SettingsPage />));
    expect(screen.queryByText(/global path mappings/i)).toBeNull();
  });

  it("Workspace summary card links to /integrations, not an in-page tab", () => {
    render(wrap(<SettingsPage />));
    const link = screen.getByRole("link", {
      name: /open path mappings on the integrations page/i,
    });
    expect(link.getAttribute("href")).toBe("/integrations");
  });
});
