/**
 * Stage 13 (audit follow-up) — Files page tags column rendering.
 *
 * Pins:
 *   - The new optional "tags" column renders up to three tag
 *     chips plus a "+N" overflow indicator.
 *   - Turning the column on adds ``include_tags=true`` to the
 *     outgoing request.
 *   - Turning it off does NOT — verifying the join is column-
 *     visibility-gated.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
import { useFilesPrefs } from "@/stores/filesPrefsStore";

const FILE_TAGGY = {
  id: "m-r1",
  library_id: "lib-1",
  path: "/data/Movies/a.mkv",
  relative_path: "Movies/a.mkv",
  filename: "a.mkv",
  extension: "mkv",
  size_bytes: 1_000_000,
  mtime: "2026-05-01T00:00:00Z",
  category: "media",
  severity: "warn",
  severity_rank: 30,
  container: "matroska",
  video_codec: "hevc",
  audio_codec: "aac",
  width: 1920,
  height: 1080,
  has_subtitles: false,
  is_orphaned: false,
  quarantined: false,
  // Five tags so the overflow is exercised.
  tags: ["4K", "archive", "needs-review", "rare", "watched"],
};

const FILE_NO_TAGS = {
  ...FILE_TAGGY,
  id: "m-r2",
  filename: "b.mkv",
  path: "/data/Movies/b.mkv",
  tags: [],
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

function mediaCallPaths(): string[] {
  return apiGet.mock.calls
    .map(([p]) => p as string)
    .filter((p) => typeof p === "string" && p.startsWith("/media"));
}

function lastMediaPath(): string | undefined {
  return mediaCallPaths().at(-1);
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  if (typeof window !== "undefined") {
    window.history.replaceState({}, "", "/files");
  }
  // Reset prefs to known defaults — no tags column unless a test
  // turns it on.
  useFilesPrefs.setState({
    visibleColumns: [
      "filename",
      "category",
      "severity",
      "size",
      "codec",
      "resolution",
      "subs",
    ],
    pageSize: 50,
    sort: { key: "severity", dir: "desc" },
  });
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/libraries") return [{ id: "lib-1", name: "Movies" }];
    if (path.startsWith("/dashboard/categories")) return [];
    if (path.startsWith("/optimization/profiles")) return [];
    if (path.startsWith("/media") && !path.includes("/m-")) {
      return {
        items: [FILE_TAGGY, FILE_NO_TAGS],
        total: 2,
        offset: 0,
        limit: 50,
      };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 13 — tags column", () => {
  it("renders up to three chips plus a +N overflow when enabled", async () => {
    useFilesPrefs.setState((s) => ({
      visibleColumns: [...s.visibleColumns, "tags"],
    }));

    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");

    // First three tag names render as chips.
    expect(screen.getByText("4K")).toBeInTheDocument();
    expect(screen.getByText("archive")).toBeInTheDocument();
    expect(screen.getByText("needs-review")).toBeInTheDocument();
    // Fourth + fifth are collapsed into overflow.
    expect(screen.queryByText("rare")).toBeNull();
    expect(screen.queryByText("watched")).toBeNull();
    // Overflow chip.
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("turning on the column adds include_tags=true to the request", async () => {
    useFilesPrefs.setState((s) => ({
      visibleColumns: [...s.visibleColumns, "tags"],
    }));

    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    const path = lastMediaPath()!;
    expect(path).toContain("include_tags=true");
  });

  it("keeping the column off does NOT include include_tags", async () => {
    // Default prefs (tags column off).
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    const path = lastMediaPath()!;
    expect(path).not.toContain("include_tags");
  });
});
