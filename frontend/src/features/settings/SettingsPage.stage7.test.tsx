/**
 * Stage 7 (audit follow-up) — SettingsPage layout tests.
 *
 * Pins the three SettingsPage restructure changes:
 *   1. System tab now hosts a sub-tab strip; switching sub-tabs
 *      shows the matching panel.
 *   2. The Workspace tab has a Path-mappings summary card whose
 *      "Open" button jumps to the Integrations tab (without
 *      navigating).
 *   3. The Security tab has an Account-security card with a link
 *      to /account.
 *
 * Networking is mocked at apiClient so the page can render the
 * full sub-tab tree without 404s.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
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
    if (path.startsWith("/system/runtime-settings/describe")) {
      return { fields: [], categories: [] };
    }
    if (path.startsWith("/system/runtime-settings/values")) return {};
    if (path.startsWith("/system/runtime-settings/secrets")) return [];
    if (path.startsWith("/system/path-mappings")) {
      return { integrations: [] };
    }
    if (path.startsWith("/system/config")) {
      return { sections: [] };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 7 — SettingsPage restructure", () => {
  it("renders the three top-level tabs (v1.9 dropped the Integrations sub-tab)", () => {
    // v1.9 Stage 2.1 — the "Integrations" tab on /settings was
    // retired; its content (PathMappingsPanel) moved to
    // /integrations. The Settings tabstrip is now three buttons:
    // Workspace, System, Security.
    render(wrap(<SettingsPage />));
    const tablist = screen.getByRole("tablist", { name: /settings sections/i });
    expect(
      within(tablist).getByRole("tab", { name: /workspace/i }),
    ).toBeInTheDocument();
    expect(
      within(tablist).getByRole("tab", { name: /system/i }),
    ).toBeInTheDocument();
    expect(
      within(tablist).getByRole("tab", { name: /security/i }),
    ).toBeInTheDocument();
    // No Integrations tab inside the settings strip.
    expect(
      within(tablist).queryByRole("tab", { name: /integrations/i }),
    ).toBeNull();
  });

  it("clicking System tab reveals the sub-tab strip", () => {
    render(wrap(<SettingsPage />));
    fireEvent.click(
      screen.getByRole("tab", { name: /^system$/i }),
    );
    const subStrip = screen.getByRole("tablist", {
      name: /system sub-sections/i,
    });
    expect(
      within(subStrip).getByRole("tab", { name: /runtime/i }),
    ).toBeInTheDocument();
    expect(
      within(subStrip).getByRole("tab", { name: /secrets/i }),
    ).toBeInTheDocument();
    expect(
      within(subStrip).getByRole("tab", { name: /system config/i }),
    ).toBeInTheDocument();
    expect(
      within(subStrip).getByRole("tab", { name: /housekeeping/i }),
    ).toBeInTheDocument();
  });

  it("System sub-tab default is Runtime (aria-selected=true)", () => {
    render(wrap(<SettingsPage />));
    fireEvent.click(screen.getByRole("tab", { name: /^system$/i }));
    const subStrip = screen.getByRole("tablist", {
      name: /system sub-sections/i,
    });
    const runtimeTab = within(subStrip).getByRole("tab", {
      name: /runtime/i,
    });
    expect(runtimeTab.getAttribute("aria-selected")).toBe("true");
  });

  it("switching to Housekeeping sub-tab flips aria-selected", () => {
    render(wrap(<SettingsPage />));
    fireEvent.click(screen.getByRole("tab", { name: /^system$/i }));
    const subStrip = screen.getByRole("tablist", {
      name: /system sub-sections/i,
    });
    fireEvent.click(
      within(subStrip).getByRole("tab", { name: /housekeeping/i }),
    );
    expect(
      within(subStrip)
        .getByRole("tab", { name: /housekeeping/i })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      within(subStrip)
        .getByRole("tab", { name: /runtime/i })
        .getAttribute("aria-selected"),
    ).toBe("false");
  });

  it("Workspace tab includes a Path-mappings summary card that links to /integrations", () => {
    // v1.9 Stage 2.1 — what used to be an in-page tab toggle is now
    // a navigation link to /integrations (the PathMappingsPanel's
    // new home).
    render(wrap(<SettingsPage />));
    const link = screen.getByRole("link", {
      name: /open path mappings on the integrations page/i,
    });
    expect(link.getAttribute("href")).toBe("/integrations");
  });

  it("Security tab renders the AccountSecurityCard with a link to /account", () => {
    render(wrap(<SettingsPage />));
    fireEvent.click(screen.getByRole("tab", { name: /^security$/i }));
    // The card title is "Account security".
    expect(screen.getByText(/account security/i)).toBeInTheDocument();
    // It has an anchor linking to /account.
    const link = screen.getByRole("link", {
      name: /open account settings/i,
    });
    expect(link.getAttribute("href")).toBe("/account");
  });
});
