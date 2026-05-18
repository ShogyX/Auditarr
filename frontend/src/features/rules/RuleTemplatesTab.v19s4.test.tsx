/**
 * v1.9 Stage 4.4 — Templates tab content.
 *
 * Pins:
 *   1. Renders rows with priority + name + description.
 *   2. Empty state when no templates.
 *   3. Loading + error states.
 *   4. "Use template" POSTs to /rule-templates/{id}/use and
 *      navigates to the created rule's editor.
 *   5. Error response surfaces inline on the row; navigation
 *      does NOT happen on error.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string) => apiPost(path),
    put: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    delete: vi.fn(async () => null),
  },
  ApiError: class extends Error {},
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    accessToken: "tok",
    refreshToken: "ref",
    user: { id: "u1", role: "admin" as const, email: "a@b.c", username: "admin" },
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

import { RuleTemplatesTab } from "@/features/rules/RuleTemplatesTab";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location-probe" data-path={loc.pathname} />;
}

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/rules"]}>
        <Routes>
          <Route
            path="/rules"
            element={
              <>
                {child}
                <LocationProbe />
              </>
            }
          />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const TEMPLATES = [
  {
    id: "t1",
    name: "Likely transcode (Plex/Jellyfin)",
    description: "Files that often force a transcode.",
    priority: 75,
    definition: { match: { all: [] }, actions: [] },
    seeded_at: "2026-05-17T10:00:00Z",
    created_at: "2026-05-17T10:00:00Z",
    updated_at: "2026-05-17T10:00:00Z",
  },
  {
    id: "t2",
    name: "Unplayable / Unsupported (Plex/Jellyfin)",
    description: "MPEG-2 in MP4 etc.",
    priority: 15,
    definition: { match: { all: [] }, actions: [] },
    seeded_at: "2026-05-17T10:00:00Z",
    created_at: "2026-05-17T10:00:00Z",
    updated_at: "2026-05-17T10:00:00Z",
  },
];

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/rule-templates") return TEMPLATES;
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 4.4 — RuleTemplatesTab", () => {
  it("renders each template with its priority + description", async () => {
    render(wrap(<RuleTemplatesTab />));
    expect(
      await screen.findByText("Likely transcode (Plex/Jellyfin)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Unplayable / Unsupported (Plex/Jellyfin)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Files that often force a transcode."),
    ).toBeInTheDocument();
  });

  it("renders empty state when no templates are seeded", async () => {
    apiGet.mockImplementation(async () => []);
    render(wrap(<RuleTemplatesTab />));
    expect(
      await screen.findByText(/no templates available/i),
    ).toBeInTheDocument();
  });

  it("renders error state when the fetch fails", async () => {
    apiGet.mockImplementation(async () => {
      throw new Error("boom");
    });
    render(wrap(<RuleTemplatesTab />));
    expect(
      await screen.findByText(/couldn't load rule templates/i),
    ).toBeInTheDocument();
  });

  it("clicking Use template POSTs and navigates to the new rule", async () => {
    apiPost.mockResolvedValue({
      id: "new-rule-id",
      name: "Likely transcode (Plex/Jellyfin)",
      priority: 75,
      enabled: true,
      definition: { match: { all: [] }, actions: [] },
      is_builtin: false,
      description: null,
      last_evaluated_at: null,
      last_match_count: 0,
      created_at: "2026-05-17T10:00:00Z",
      updated_at: "2026-05-17T10:00:00Z",
    });
    render(wrap(<RuleTemplatesTab />));
    await screen.findByText("Likely transcode (Plex/Jellyfin)");
    const useButtons = screen.getAllByRole("button", {
      name: /use template/i,
    });
    fireEvent.click(useButtons[0]!);
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/rule-templates/t1/use"),
    );
    const probe = await screen.findByTestId("location-probe");
    expect(probe).toHaveAttribute("data-path", "/rules/new-rule-id/edit");
  });

  it("surfaces an inline error and does NOT navigate on a failed POST", async () => {
    apiPost.mockRejectedValue(new Error("server says no"));
    render(wrap(<RuleTemplatesTab />));
    await screen.findByText("Likely transcode (Plex/Jellyfin)");
    const useButtons = screen.getAllByRole("button", {
      name: /use template/i,
    });
    fireEvent.click(useButtons[0]!);
    await waitFor(() =>
      expect(screen.getByText("server says no")).toBeInTheDocument(),
    );
    // Location stays put — never navigated to /rules/.../edit.
    const probe = screen.getByTestId("location-probe");
    expect(probe).toHaveAttribute("data-path", "/rules");
  });
});
