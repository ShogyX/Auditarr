/**
 * Stage 28 — Optimize profile picker tests.
 *
 * Pins:
 *
 *   - Optimize button stays disabled while profiles are loading
 *   - Disabled state + helpful title when no enabled profiles exist
 *   - Clicking the button toggles a popover listing enabled profiles
 *   - Disabled profiles are hidden from the picker (no foot-gun)
 *   - Choosing a profile POSTs the right body to /bulk-enqueue
 *   - The popover dismisses on selection and on Escape
 *   - Empty-list state is handled cleanly when the network returns []
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

import { FilesPage } from "@/features/files/FilesPage";

// ── Fixtures ─────────────────────────────────────────────────
const FILE_A = {
  id: "m-aaa",
  library_id: "lib-1",
  path: "/data/Movies/a.mkv",
  relative_path: "Movies/a.mkv",
  filename: "a.mkv",
  extension: "mkv",
  size_bytes: 1_000_000,
  mtime: "2026-05-10T12:00:00Z",
  category: "media",
  severity: "ok",
  severity_rank: 10,
  container: "matroska",
  video_codec: "h264",
  audio_codec: "aac",
  width: 1920,
  height: 1080,
  has_subtitles: false,
  is_orphaned: false,
};

const PROFILE_ENABLED = {
  id: "prof-1",
  name: "Shrink HEVC",
  description: "Reduce big HEVC files",
  enabled: true,
  settings: {},
  max_input_bytes: null,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
};

const PROFILE_DISABLED = {
  ...PROFILE_ENABLED,
  id: "prof-2",
  name: "Old Profile",
  enabled: false,
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
    if (path === "/libraries") return [{ id: "lib-1", name: "Movies" }];
    if (path === "/optimization/profiles")
      return [PROFILE_ENABLED, PROFILE_DISABLED];
    if (path.startsWith("/media")) {
      return { items: [FILE_A], total: 1, offset: 0, limit: 50 };
    }
    return null;
  });
  apiPost.mockImplementation(async (path: string) => {
    if (path === "/optimization/bulk-enqueue") {
      return {
        queued: 1,
        already_queued: 0,
        skipped_active: 0,
        files_not_found: [],
      };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

async function selectFirstRow() {
  await waitFor(() => expect(screen.queryByText("a.mkv")).toBeInTheDocument());
  const row = screen.getByText("a.mkv").closest("tr");
  expect(row).toBeTruthy();
  const checkbox = within(row as HTMLElement).getByRole("checkbox");
  fireEvent.click(checkbox);
}

// ── Tests ────────────────────────────────────────────────────

describe("OptimizeProfilePicker Stage 28", () => {
  it("clicking Optimize opens a popover listing enabled profiles only", async () => {
    render(wrap(<FilesPage />));
    await selectFirstRow();

    // Wait for the profiles query to resolve so the Optimize
    // button transitions from loading-disabled to enabled.
    const optimize = await waitFor(() => {
      const btn = screen.getByRole("button", { name: /optimize/i });
      expect(btn).not.toBeDisabled();
      return btn;
    });
    fireEvent.click(optimize);

    const menu = await screen.findByRole("menu", {
      name: /optimization profiles/i,
    });
    expect(within(menu).getByText("Shrink HEVC")).toBeInTheDocument();
    // Disabled profile must NOT appear — operators shouldn't be
    // able to enqueue against a profile that won't run.
    expect(within(menu).queryByText("Old Profile")).not.toBeInTheDocument();
  });

  it("choosing a profile POSTs to /bulk-enqueue with the right body", async () => {
    render(wrap(<FilesPage />));
    await selectFirstRow();

    const optimize = await waitFor(() => {
      const btn = screen.getByRole("button", { name: /optimize/i });
      expect(btn).not.toBeDisabled();
      return btn;
    });
    fireEvent.click(optimize);

    const menu = await screen.findByRole("menu");
    const profileBtn = within(menu).getByRole("menuitem", {
      name: /shrink hevc/i,
    });
    fireEvent.click(profileBtn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/optimization/bulk-enqueue",
        expect.objectContaining({
          media_ids: ["m-aaa"],
          profile: "Shrink HEVC",
        }),
      );
    });
  });

  it("popover dismisses after a profile is chosen", async () => {
    render(wrap(<FilesPage />));
    await selectFirstRow();

    const optimize = await waitFor(() => {
      const btn = screen.getByRole("button", { name: /optimize/i });
      expect(btn).not.toBeDisabled();
      return btn;
    });
    fireEvent.click(optimize);

    const menu = await screen.findByRole("menu");
    const profileBtn = within(menu).getByRole("menuitem", {
      name: /shrink hevc/i,
    });
    fireEvent.click(profileBtn);

    await waitFor(() => {
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });
  });

  it("popover dismisses on Escape", async () => {
    render(wrap(<FilesPage />));
    await selectFirstRow();

    const optimize = await waitFor(() => {
      const btn = screen.getByRole("button", { name: /optimize/i });
      expect(btn).not.toBeDisabled();
      return btn;
    });
    fireEvent.click(optimize);

    await screen.findByRole("menu");

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });
  });

  it("Optimize button is disabled when no enabled profiles exist", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/libraries") return [{ id: "lib-1", name: "Movies" }];
      if (path === "/optimization/profiles") return [PROFILE_DISABLED];
      if (path.startsWith("/media")) {
        return { items: [FILE_A], total: 1, offset: 0, limit: 50 };
      }
      return null;
    });

    render(wrap(<FilesPage />));
    await selectFirstRow();

    const optimize = await screen.findByRole("button", { name: /optimize/i });
    expect(optimize).toBeDisabled();
    expect(optimize.getAttribute("title")).toMatch(/no enabled.*profiles/i);
  });

  it("Optimize button is disabled when profiles list is empty", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/libraries") return [{ id: "lib-1", name: "Movies" }];
      if (path === "/optimization/profiles") return [];
      if (path.startsWith("/media")) {
        return { items: [FILE_A], total: 1, offset: 0, limit: 50 };
      }
      return null;
    });

    render(wrap(<FilesPage />));
    await selectFirstRow();

    const optimize = await screen.findByRole("button", { name: /optimize/i });
    expect(optimize).toBeDisabled();
  });
});
