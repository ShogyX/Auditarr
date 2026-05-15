/**
 * Stage 13 (audit follow-up) — IntegrationRow tag-sync affordance.
 *
 * Pins:
 *   - "Sync tags" button renders for Sonarr/Radarr/Bazarr rows only.
 *   - Button is hidden for Plex/Jellyfin rows (their manager
 *     returns [] so the button would be a no-op).
 *   - Button is hidden for non-admin users (admin-gated server-side
 *     too — the UI hides the button to spare the operator a needless
 *     403 round-trip).
 *   - Clicking the button calls ``POST /integrations/{id}/sync-tags``
 *     and surfaces the report counts in a toast.
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

// Mocked at top so import paths resolve to the mocked module.
const apiGet = vi.fn();
const apiPost = vi.fn();
const toastSpy = vi.fn();

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

vi.mock("@/lib/toast", () => ({ toast: (...args: unknown[]) => toastSpy(...args) }));

// Auth store mock — drives admin vs non-admin via a mutable state.
type AuthState = {
  tokens: { accessToken: string; refreshToken: string; expiresAt: number };
  user: { id: string; username: string; role: string };
  isHydrated: boolean;
};
const authState: AuthState = {
  tokens: { accessToken: "x", refreshToken: "x", expiresAt: Date.now() + 6e4 },
  user: { id: "u1", username: "tester", role: "admin" },
  isHydrated: true,
};
vi.mock("@/stores/authStore", () => {
  const useAuthStore = vi.fn((sel?: (s: AuthState) => unknown) =>
    typeof sel === "function" ? sel(authState) : authState,
  ) as unknown as ((sel?: (s: AuthState) => unknown) => unknown) & {
    getState: () => AuthState;
    persist: { hasHydrated: () => boolean };
  };
  useAuthStore.getState = () => authState;
  useAuthStore.persist = { hasHydrated: () => true };
  return { useAuthStore };
});

import { IntegrationRow } from "@/features/integrations/IntegrationRow";
import type { Integration } from "@/hooks/useIntegrations";

function makeIntegration(overrides: Partial<Integration> = {}): Integration {
  return {
    id: "int-1",
    name: "My integration",
    kind: "sonarr",
    enabled: true,
    health_status: "ok",
    health_detail: null,
    last_checked_at: "2026-05-14T10:00:00Z",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-14T10:00:00Z",
    ...overrides,
  } as Integration;
}

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
  toastSpy.mockReset();
  authState.user = { id: "u1", username: "tester", role: "admin" };
  // The row uses ``useCursors()`` — stub it with an empty list so
  // the "last polled" path doesn't fire for the test rows.
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/playback/cursors") return [];
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 13 — IntegrationRow Sync tags", () => {
  it("renders the button for Sonarr integrations (admin user)", () => {
    render(
      wrap(
        <IntegrationRow
          integration={makeIntegration({ kind: "sonarr" })}
          onCheck={() => {}}
          onEdit={() => {}}
          onToggle={() => {}}
          onDelete={() => {}}
        />,
      ),
    );
    expect(
      screen.getByRole("button", { name: /sync tags from/i }),
    ).toBeInTheDocument();
  });

  it("renders the button for Radarr and Bazarr", () => {
    for (const kind of ["radarr", "bazarr"] as const) {
      const { unmount } = render(
        wrap(
          <IntegrationRow
            integration={makeIntegration({
              id: `int-${kind}`,
              kind,
              name: `My ${kind}`,
            })}
            onCheck={() => {}}
            onEdit={() => {}}
            onToggle={() => {}}
            onDelete={() => {}}
          />,
        ),
      );
      expect(
        screen.getByRole("button", { name: new RegExp(`sync tags from My ${kind}`, "i") }),
      ).toBeInTheDocument();
      unmount();
    }
  });

  it("HIDES the button for Plex and Jellyfin", () => {
    for (const kind of ["plex", "jellyfin"] as const) {
      const { unmount } = render(
        wrap(
          <IntegrationRow
            integration={makeIntegration({ id: `int-${kind}`, kind, name: `My ${kind}` })}
            onCheck={() => {}}
            onEdit={() => {}}
            onToggle={() => {}}
            onDelete={() => {}}
          />,
        ),
      );
      expect(
        screen.queryByRole("button", { name: new RegExp(`sync tags from`, "i") }),
      ).toBeNull();
      unmount();
    }
  });

  it("HIDES the button for non-admin users even on Sonarr", () => {
    authState.user = { id: "u2", username: "viewer", role: "user" };
    render(
      wrap(
        <IntegrationRow
          integration={makeIntegration({ kind: "sonarr" })}
          onCheck={() => {}}
          onEdit={() => {}}
          onToggle={() => {}}
          onDelete={() => {}}
        />,
      ),
    );
    expect(
      screen.queryByRole("button", { name: /sync tags from/i }),
    ).toBeNull();
  });

  it("clicking the button POSTs to the sync-tags endpoint and toasts the report", async () => {
    apiPost.mockImplementation(async (path: string) => {
      if (path === "/integrations/int-1/sync-tags") {
        return {
          integration_id: "int-1",
          inserted: 7,
          removed: 2,
          title_count: 9,
          skipped_no_path: 1,
        };
      }
      return null;
    });

    render(
      wrap(
        <IntegrationRow
          integration={makeIntegration({ kind: "sonarr" })}
          onCheck={() => {}}
          onEdit={() => {}}
          onToggle={() => {}}
          onDelete={() => {}}
        />,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /sync tags from/i }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/integrations/int-1/sync-tags",
        {},
      );
    });
    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalled();
    });
    // First arg is the message string; verify the counts surfaced.
    const msg = toastSpy.mock.calls[0]![0] as string;
    expect(msg).toMatch(/inserted 7/i);
    expect(msg).toMatch(/removed 2/i);
    // The "1 skipped, no path" detail surfaces too.
    expect(msg).toMatch(/skipped/i);
  });
});
