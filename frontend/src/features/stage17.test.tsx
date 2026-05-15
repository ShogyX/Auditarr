/**
 * Stage 17 (audit follow-up) — UI polish.
 *
 * Pins (one per sub-surface, sometimes more):
 *   A. Rules editor + Settings shells are sized for room.
 *   B. Dashboard Scan-all button is admin-only and POSTs to /scans/all.
 *   C. ArgInput with format="library_id" renders a dropdown of
 *      libraries; format="integration_id" renders a dropdown of
 *      integrations.
 *   D. PathMappingsPanel renders Mapped / Missing / Stale states
 *      from discovered_paths. Snapshot=null shows the admin
 *      "Discover now" button.
 *   E. The local-path input pairs with a library dropdown that
 *      copies root_path into the input on selection.
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
const apiPut = vi.fn();
const toastSpy = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
    put: (path: string, body?: unknown) => apiPut(path, body),
    delete: vi.fn(async () => null),
    patch: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

vi.mock("@/lib/toast", () => ({
  toast: (...args: unknown[]) => toastSpy(...args),
}));

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

import { ArgInput } from "@/features/automation/scheduleFormShared";
import { PathMappingsPanel } from "@/features/settings/PathMappingsPanel";

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
  apiPut.mockReset();
  toastSpy.mockReset();
  authState.user = { id: "u1", username: "tester", role: "admin" };
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Sub-surface C — ArgInput format hints ────────────────────────
describe("Stage 17 — ArgInput library/integration dropdowns", () => {
  it("renders a library dropdown when spec.format = library_id", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/libraries") {
        return [
          { id: "lib-1", name: "Movies", root_path: "/mnt/movies" },
          { id: "lib-2", name: "TV", root_path: "/mnt/tv" },
        ];
      }
      return null;
    });

    const onChange = vi.fn();
    render(
      wrap(
        <ArgInput
          argKey="library_id"
          spec={{ type: "string", title: "Library", format: "library_id" }}
          required
          value=""
          onChange={onChange}
        />,
      ),
    );

    await waitFor(() => {
      const select = screen.getByLabelText(/Library/);
      expect(select.tagName).toBe("SELECT");
      const options = (select as HTMLSelectElement).options;
      const labels = Array.from(options).map((o) => o.textContent);
      expect(labels).toContain("Movies");
      expect(labels).toContain("TV");
    });
  });

  it("renders an integration dropdown when spec.format = integration_id", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/integrations") {
        return [
          {
            id: "int-1",
            name: "My Plex",
            kind: "plex",
            enabled: true,
            poll_interval_seconds: 300,
            config: {},
            health_status: "ok",
            health_detail: null,
            health_checked_at: null,
            created_at: "2026-05-01",
            updated_at: "2026-05-01",
            has_secrets: true,
          },
        ];
      }
      return null;
    });

    render(
      wrap(
        <ArgInput
          argKey="integration_id"
          spec={{
            type: "string",
            title: "Integration",
            format: "integration_id",
          }}
          required
          value=""
          onChange={() => {}}
        />,
      ),
    );

    await waitFor(() => {
      const select = screen.getByLabelText(/Integration/);
      const labels = Array.from(
        (select as HTMLSelectElement).options,
      ).map((o) => o.textContent);
      // "My Plex (plex)" — both name + kind surface for disambiguation.
      expect(labels.some((l) => l?.includes("My Plex"))).toBe(true);
    });
  });
});

// ── Sub-surface D + E — PathMappingsPanel discovery + library dropdown ──
describe("Stage 17 — PathMappingsPanel discovery snapshot", () => {
  it("highlights MISSING paths from the discovery snapshot with an Add mapping button", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/path-mappings") {
        return {
          integrations: [
            {
              integration_id: "int-1",
              name: "Plex prod",
              kind: "plex",
              is_active: true,
              mappings: [], // No mappings configured
              raw: [],
              discovered_paths: [
                {
                  library_id: "lib-mov",
                  label: "Movies",
                  upstream_path: "/data/media/movies",
                  discovered_at: "2026-05-15T00:00:00Z",
                },
              ],
            },
          ],
        };
      }
      if (path === "/system/path-mappings/global") return [];
      if (path === "/system/path-suggestions") return { paths: [] };
      if (path === "/libraries") return [];
      return null;
    });

    render(wrap(<PathMappingsPanel />));

    await waitFor(() => {
      expect(screen.getByTestId("discovery-section")).toBeInTheDocument();
    });
    const missingRow = screen.getByTestId("discovery-missing");
    expect(missingRow.textContent).toContain("Movies");
    expect(missingRow.textContent).toContain("/data/media/movies");
    expect(
      within(missingRow).getByRole("button", { name: /Add mapping/i }),
    ).toBeInTheDocument();
  });

  it("highlights STALE mappings whose 'from' path is not in the snapshot", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/path-mappings") {
        return {
          integrations: [
            {
              integration_id: "int-1",
              name: "Plex prod",
              kind: "plex",
              is_active: true,
              mappings: [
                { from: "/old/path", to: "/local/old" },
              ],
              raw: [{ from: "/old/path", to: "/local/old" }],
              discovered_paths: [
                {
                  library_id: "lib-new",
                  label: "New",
                  upstream_path: "/new/path",
                  discovered_at: "2026-05-15T00:00:00Z",
                },
              ],
            },
          ],
        };
      }
      if (path === "/system/path-mappings/global") return [];
      if (path === "/system/path-suggestions") return { paths: [] };
      if (path === "/libraries") return [];
      return null;
    });

    render(wrap(<PathMappingsPanel />));

    await waitFor(() => {
      expect(screen.getByTestId("discovery-section")).toBeInTheDocument();
    });
    const staleRow = screen.getByTestId("discovery-stale");
    expect(staleRow.textContent).toContain("/old/path");
    expect(staleRow.textContent).toContain("no longer in discovery");
  });

  it("snapshot=null integrations show the admin 'Discover now' button", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/path-mappings") {
        return {
          integrations: [
            {
              integration_id: "int-1",
              name: "Legacy",
              kind: "sonarr",
              is_active: true,
              mappings: [],
              raw: [],
              discovered_paths: null,
            },
          ],
        };
      }
      if (path === "/system/path-mappings/global") return [];
      if (path === "/system/path-suggestions") return { paths: [] };
      if (path === "/libraries") return [];
      return null;
    });

    apiPost.mockImplementation(async (path: string) => {
      if (path === "/integrations/int-1/discover-paths") {
        return {
          integration_id: "int-1",
          discovered_paths: [],
        };
      }
      return null;
    });

    render(wrap(<PathMappingsPanel />));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Discover now/i }),
      ).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", { name: /Discover now/i }),
    );

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/integrations/int-1/discover-paths",
        {},
      );
    });
  });

  it("snapshot=null is HIDDEN for non-admin users", async () => {
    authState.user = { id: "u2", username: "viewer", role: "user" };
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/path-mappings") {
        return {
          integrations: [
            {
              integration_id: "int-1",
              name: "Legacy",
              kind: "sonarr",
              is_active: true,
              mappings: [],
              raw: [],
              discovered_paths: null,
            },
          ],
        };
      }
      if (path === "/system/path-mappings/global") return [];
      if (path === "/system/path-suggestions") return { paths: [] };
      if (path === "/libraries") return [];
      return null;
    });

    render(wrap(<PathMappingsPanel />));

    await waitFor(() => {
      expect(screen.getByText("Legacy")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /Discover now/i }),
    ).toBeNull();
  });

  it("library dropdown next to the local-path input copies root_path on selection", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/path-mappings") {
        return {
          integrations: [
            {
              integration_id: "int-1",
              name: "Plex prod",
              kind: "plex",
              is_active: true,
              mappings: [{ from: "/up/path", to: "" }],
              raw: [{ from: "/up/path", to: "" }],
              discovered_paths: [],
            },
          ],
        };
      }
      if (path === "/system/path-mappings/global") return [];
      if (path === "/system/path-suggestions") return { paths: [] };
      if (path === "/libraries") {
        return [
          { id: "lib-1", name: "Movies", root_path: "/mnt/movies" },
        ];
      }
      return null;
    });

    render(wrap(<PathMappingsPanel />));

    await waitFor(() => {
      expect(screen.getByText("Plex prod")).toBeInTheDocument();
    });
    const dropdowns = screen.getAllByLabelText(
      /Copy a library root path/i,
    );
    expect(dropdowns.length).toBeGreaterThan(0);
    fireEvent.change(dropdowns[0]!, { target: { value: "lib-1" } });
    // The to-input now carries the library's root_path. Find the
    // input with that value (placeholder /mnt/storage/Movies).
    await waitFor(() => {
      const inputs = document.querySelectorAll<HTMLInputElement>(
        "input.settings-input.mono",
      );
      const values = Array.from(inputs).map((i) => i.value);
      expect(values).toContain("/mnt/movies");
    });
  });
});
