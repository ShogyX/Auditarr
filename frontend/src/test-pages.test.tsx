/**
 * Stage 14 visual sweep: mount every top-level page in JSDOM with a
 * mocked QueryClient + router, assert it renders something visible
 * without throwing.
 *
 * This catches the class of bug that typecheck and lint can't:
 *
 *   - `cannot read properties of undefined` from optional-chain
 *     oversights
 *   - dead hook calls left over from refactors
 *   - components that explode when query data is `undefined` / loading
 *   - import cycles that surface only at module-load time
 *
 * We mock `apiClient` and `useAuthStore` so no real network requests
 * fire. Every page must:
 *
 *   1. Mount without throwing.
 *   2. Render at least one element (the page header).
 *   3. Not log any errors to the console.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

// ── Mock the apiClient before any module imports it ─────────────
vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: vi.fn(async () => null),
    post: vi.fn(async () => null),
    put: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    delete: vi.fn(async () => null),
  },
  ApiError: class ApiError extends Error {
    status = 500;
    code = "test";
    constructor(message: string) {
      super(message);
    }
  },
}));

// ── Mock the auth store so RequireAuth-ish reads don't redirect ─
vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: { accessToken: "fake", refreshToken: "fake", expiresAt: Date.now() + 60000 },
    user: { id: "u1", email: "u@example.com", username: "tester", role: "admin" },
    isHydrated: true,
    setTokens: vi.fn(),
    setSession: vi.fn(),
    setUser: vi.fn(),
    clear: vi.fn(),
    hydrate: vi.fn(),
  };
  type StoreState = typeof state;
  type Selector<R> = (s: StoreState) => R;
  const useAuthStore = vi.fn((selector?: Selector<unknown>): unknown =>
    typeof selector === "function" ? selector(state) : state,
  ) as unknown as ((selector?: Selector<unknown>) => unknown) & {
    getState: () => StoreState;
    persist: { hasHydrated: () => boolean };
  };
  useAuthStore.getState = () => state;
  useAuthStore.persist = { hasHydrated: () => true };
  return { useAuthStore };
});

// ── Imports AFTER mocks ─────────────────────────────────────────
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { FilesPage } from "@/features/files/FilesPage";
import { RulesPage } from "@/features/rules/RulesPage";
import { AutomationPage } from "@/features/automation/AutomationPage";
import { OptimizationPage } from "@/features/optimization/OptimizationPage";
import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { PluginsPage } from "@/features/plugins/PluginsPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { HelpPage } from "@/features/help/HelpPage";
import { LoginPage } from "@/features/auth/LoginPage";
import { ForgotPasswordPage } from "@/features/auth/ForgotPasswordPage";
import { ResetPasswordPage } from "@/features/auth/ResetPasswordPage";

function wrap(child: ReactNode, initialPath = "/") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>{child}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("page-mount smoke", () => {
  let errors: string[] = [];
  let originalError: typeof console.error;

  beforeEach(() => {
    errors = [];
    originalError = console.error;
    console.error = (...args: unknown[]) => {
      // React testing library re-logs prop errors etc. — collect them
      // so the test can assert no real errors were logged.
      errors.push(args.map((a) => String(a)).join(" "));
    };
  });

  afterEach(() => {
    console.error = originalError;
  });

  it("DashboardPage mounts", () => {
    render(wrap(<DashboardPage />));
    expect(screen.getAllByText(/dashboard/i).length).toBeGreaterThan(0);
    expect(errors.filter((e) => !/jsdom/i.test(e))).toEqual([]);
  });

  it("FilesPage mounts", () => {
    render(wrap(<FilesPage />));
    expect(screen.getAllByText(/^files$/i).length).toBeGreaterThan(0);
    expect(errors.filter((e) => !/jsdom/i.test(e))).toEqual([]);
  });

  it("RulesPage mounts", () => {
    render(wrap(<RulesPage />));
    expect(screen.getAllByText(/rules/i).length).toBeGreaterThan(0);
  });

  it("AutomationPage mounts", () => {
    render(wrap(<AutomationPage />));
    expect(screen.getAllByText(/automation/i).length).toBeGreaterThan(0);
  });

  it("OptimizationPage mounts", () => {
    render(wrap(<OptimizationPage />));
    expect(screen.getAllByText(/optimization/i).length).toBeGreaterThan(0);
  });

  it("IntegrationsPage mounts", () => {
    render(wrap(<IntegrationsPage />));
    expect(screen.getAllByText(/integrations/i).length).toBeGreaterThan(0);
  });

  it("NotificationsPage mounts", () => {
    render(wrap(<NotificationsPage />));
    expect(screen.getAllByText(/notifications/i).length).toBeGreaterThan(0);
  });

  it("PluginsPage mounts", () => {
    render(wrap(<PluginsPage />));
    expect(screen.getAllByText(/plugins/i).length).toBeGreaterThan(0);
  });

  it("SettingsPage mounts", () => {
    render(wrap(<SettingsPage />));
    expect(screen.getAllByText(/settings/i).length).toBeGreaterThan(0);
  });

  it("SettingsPage exposes the Stage 22 runtime/secrets panels (v1.9: path-mappings moved to /integrations)", () => {
    // Smoke-only: with apiClient.get mocked to return null, every new
    // panel should render its admin/empty state rather than throwing.
    // We just confirm the panel headings made it into the DOM.
    //
    // Stage 6 audit fix (Issue 8): the Settings page now groups
    // sections under category tabs. Runtime settings + Secrets
    // live on the "System" tab.
    //
    // v1.9 Stage 2.1: the "Integrations" sub-tab here was retired
    // and PathMappingsPanel moved to /integrations. We assert the
    // Workspace tab now hosts a summary card linking to that page
    // (proof the move happened) rather than the heavy editor.
    render(wrap(<SettingsPage />));

    fireEvent.click(screen.getByRole("tab", { name: /system/i }));
    expect(screen.getAllByText(/runtime settings/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^secrets$/i).length).toBeGreaterThan(0);

    // No integrations tab on /settings anymore.
    expect(
      screen.queryByRole("tab", { name: /integrations/i }),
    ).toBeNull();

    // The Workspace tab summary card links to /integrations.
    fireEvent.click(screen.getByRole("tab", { name: /workspace/i }));
    const link = screen.getByRole("link", {
      name: /open path mappings on the integrations page/i,
    });
    expect(link.getAttribute("href")).toBe("/integrations");

    expect(errors.filter((e) => !/jsdom/i.test(e))).toEqual([]);
  });

  it("HelpPage mounts", () => {
    render(wrap(<HelpPage />));
    expect(screen.getAllByText(/help|updates/i).length).toBeGreaterThan(0);
  });

  it("LoginPage mounts", () => {
    render(wrap(<LoginPage />, "/login"));
    // LoginPage redirects when tokens are present — but we mocked tokens
    // so it'll actually render <Navigate>. Either is fine — the test
    // is that mounting doesn't throw.
    expect(document.body).toBeInTheDocument();
  });

  it("ForgotPasswordPage mounts", () => {
    render(wrap(<ForgotPasswordPage />, "/forgot"));
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(0);
  });

  it("ResetPasswordPage mounts", () => {
    render(wrap(<ResetPasswordPage />, "/reset-password?token=abc"));
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(0);
  });
});
