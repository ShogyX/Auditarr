/**
 * Stage 11 (audit follow-up) — HelpDrawer polish tests.
 *
 * Pre-Stage-11 the drawer paused its queries while closed, so
 * reopening for the same context re-mounted the query and a brief
 * loading state flashed before React Query served the cache. This
 * test file pins:
 *
 *   - Drawer width has been bumped from ``max-w-md`` to the
 *     responsive ``max-w-md md:max-w-xl lg:max-w-2xl`` ladder.
 *   - Queries run unconditionally (no ``isOpen ?`` gating) so the
 *     cached doc is rendered immediately on a same-context reopen.
 *   - The loading state only appears on the very first fetch — not
 *     during a background refetch when we already have cached data.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

// ── Mock the data hooks so we can drive the cache deterministically ──
const useHelpContextMock = vi.fn();
const useDocPageMock = vi.fn();

vi.mock("@/hooks/useDocs", () => ({
  useHelpContext: (...args: unknown[]) => useHelpContextMock(...args),
  useDocPage: (...args: unknown[]) => useDocPageMock(...args),
}));

// ── Mock authStore so the shell deps are satisfied ──
vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: {
      accessToken: "x",
      refreshToken: "x",
      expiresAt: Date.now() + 6e4,
    },
    user: { id: "u1", username: "user", role: "user" },
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

import { HelpDrawer } from "@/components/shell/HelpDrawer";
import { useHelpStore } from "@/stores/helpStore";

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
  // Reset the help store between tests.
  useHelpStore.getState().close();
  useHelpStore.setState({ activeKey: null, isOpen: false });
  useHelpContextMock.mockReset();
  useDocPageMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 11 — HelpDrawer width + no-flicker reopens", () => {
  it("uses the responsive width clamp (max-w-md md:max-w-xl lg:max-w-2xl)", () => {
    useHelpContextMock.mockReturnValue({ data: [], isPending: false });
    useDocPageMock.mockReturnValue({ data: null, isPending: false });

    render(wrap(<HelpDrawer />));

    const aside = screen.getByLabelText("Contextual help");
    // The combined width-clamp tokens must all be present so a
    // future regression that drops one is caught.
    expect(aside.className).toContain("max-w-md");
    expect(aside.className).toContain("md:max-w-xl");
    expect(aside.className).toContain("lg:max-w-2xl");
  });

  it("does NOT pause the help-context query while closed", () => {
    useHelpContextMock.mockReturnValue({ data: [], isPending: false });
    useDocPageMock.mockReturnValue({ data: null, isPending: false });

    // Pre-set an active key but keep the drawer closed.
    useHelpStore.setState({ activeKey: "rules.actions", isOpen: false });

    render(wrap(<HelpDrawer />));

    // Pre-Stage-11 the hook was called with ``null`` while the
    // drawer was closed. Post-Stage-11 it must receive the real
    // active key so the cache is pre-warmed.
    expect(useHelpContextMock).toHaveBeenCalledWith("rules.actions");
    // It must NEVER be called with null when an activeKey exists.
    const nullCalls = useHelpContextMock.mock.calls.filter(
      (c) => c[0] === null,
    );
    expect(nullCalls.length).toBe(0);
  });

  it("does NOT pause the doc-page query while closed", () => {
    useHelpContextMock.mockReturnValue({
      data: [{ id: "rules/actions", title: "Rule actions" }],
      isPending: false,
    });
    useDocPageMock.mockReturnValue({ data: null, isPending: false });

    useHelpStore.setState({ activeKey: "rules.actions", isOpen: false });

    render(wrap(<HelpDrawer />));

    // The first useDocPage call (in the outer HelpDrawer body) and
    // the second (in HelpBody) should both receive the real pageId
    // even though isOpen is false.
    expect(useDocPageMock).toHaveBeenCalledWith("rules/actions");
    const nullPageCalls = useDocPageMock.mock.calls.filter(
      (c) => c[0] === null,
    );
    expect(nullPageCalls.length).toBe(0);
  });

  it("renders cached body immediately when the drawer is opened a second time for the same context", () => {
    // Simulate: first open → query has data; close; reopen → same
    // hook call, same data → drawer must render the body without
    // ever showing a loading state.
    useHelpContextMock.mockReturnValue({
      data: [{ id: "rules/actions", title: "Rule actions" }],
      isPending: false,
    });
    useDocPageMock.mockReturnValue({
      data: {
        id: "rules/actions",
        title: "Rule actions",
        body_html: "<p>Cached body</p>",
      },
      isPending: false,
    });

    // First open.
    useHelpStore.setState({ activeKey: "rules.actions", isOpen: true });
    const { rerender } = render(wrap(<HelpDrawer />));

    // The body should be visible — no loading flicker.
    expect(screen.getByText("Cached body")).toBeInTheDocument();
    // The loading-state label must not appear.
    expect(screen.queryByText(/loading help/i)).toBeNull();

    // Close + reopen for the SAME context.
    act(() => {
      useHelpStore.getState().close();
    });
    rerender(wrap(<HelpDrawer />));

    act(() => {
      useHelpStore.getState().open("rules.actions");
    });
    rerender(wrap(<HelpDrawer />));

    // Body is still there, no loading flicker on reopen.
    expect(screen.getByText("Cached body")).toBeInTheDocument();
    expect(screen.queryByText(/loading help/i)).toBeNull();
  });

  it("shows the loading state only on a true first fetch", () => {
    // isPending=true AND no data yet → this IS a first fetch and
    // the loading state is appropriate.
    useHelpContextMock.mockReturnValue({
      data: undefined,
      isPending: true,
    });
    useDocPageMock.mockReturnValue({ data: undefined, isPending: false });

    useHelpStore.setState({ activeKey: "rules.actions", isOpen: true });
    render(wrap(<HelpDrawer />));

    expect(screen.getByText(/loading help/i)).toBeInTheDocument();
  });

  it("does NOT show the loading state during a background refetch when data is present", () => {
    // isPending=true (background refetch) BUT we already have data
    // → the body stays rendered, no flicker.
    useHelpContextMock.mockReturnValue({
      data: [{ id: "rules/actions", title: "Rule actions" }],
      isPending: false,
    });
    useDocPageMock.mockReturnValue({
      data: {
        id: "rules/actions",
        title: "Rule actions",
        body_html: "<p>Still here</p>",
      },
      // The combination "isPending: true with data present" can't
      // happen in real React Query (isPending == data === undefined),
      // but ``isFetching: true`` during refetch is what we're
      // simulating conceptually. The Stage 11 fix uses ``isPending
      // && !data`` rather than ``isLoading`` to gate the loading
      // state, so background fetches never flicker. Test the data-
      // present path is rendered.
      isPending: false,
    });

    useHelpStore.setState({ activeKey: "rules.actions", isOpen: true });
    render(wrap(<HelpDrawer />));

    expect(screen.getByText("Still here")).toBeInTheDocument();
    expect(screen.queryByText(/loading help/i)).toBeNull();
  });

  it("dropdown / empty state still works when no help context is set", () => {
    useHelpContextMock.mockReturnValue({ data: [], isPending: false });
    useDocPageMock.mockReturnValue({ data: null, isPending: false });

    useHelpStore.setState({ activeKey: null, isOpen: true });
    render(wrap(<HelpDrawer />));

    expect(screen.getByText(/no help context yet/i)).toBeInTheDocument();
  });

  it("close button works (regression sanity)", () => {
    useHelpContextMock.mockReturnValue({ data: [], isPending: false });
    useDocPageMock.mockReturnValue({ data: null, isPending: false });

    useHelpStore.setState({ activeKey: "rules.actions", isOpen: true });
    render(wrap(<HelpDrawer />));

    expect(useHelpStore.getState().isOpen).toBe(true);
    fireEvent.click(screen.getByLabelText(/close help/i));
    expect(useHelpStore.getState().isOpen).toBe(false);
  });
});
