/**
 * Stage 13 (audit follow-up) — FileDetailDrawer tags section.
 *
 * Pins:
 *   - Drawer renders the Tags section grouped by source when the
 *     file has tags.
 *   - Section is OMITTED entirely when the file has no tags
 *     (mirrors Stage 12 playback pattern — no noisy empty state).
 *   - Casing is preserved: "4K" and "4k" both render distinctly.
 *   - Source labels render the human-friendly form ("From rules",
 *     "From Sonarr", "Manual").
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

import { FileDetailDrawer } from "@/features/files/FileDetailDrawer";

const SUMMARY = {
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

const DETAIL = {
  ...SUMMARY,
  duration_seconds: 3600,
  bitrate_kbps: 8000,
  subtitle_codec: null,
  framerate: 23.976,
  subtitle_languages: null,
  audio_languages: ["eng"],
  probe: null,
  probe_failed: false,
  probe_error: null,
  last_scan_id: "scan-1",
  seen_at: "2026-05-10T12:00:00Z",
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-10T12:00:00Z",
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

function mockGets(tags: unknown[]) {
  apiGet.mockImplementation(async (path: string) => {
    if (path === `/media/${SUMMARY.id}`) return DETAIL;
    if (path === `/media/${SUMMARY.id}/evaluations`) return [];
    if (path === `/media/${SUMMARY.id}/tags`) return tags;
    if (path.startsWith("/playback/events?")) {
      return { items: [], total: 0, offset: 0, limit: 10 };
    }
    return null;
  });
}

beforeEach(() => {
  apiGet.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("FileDetailDrawer Stage 13 — tags section", () => {
  it("renders Tags section grouped by source", async () => {
    mockGets([
      { id: 1, name: "watched", source: "manual", created_at: "2026-05-01T00:00:00Z" },
      { id: 2, name: "needs-review", source: "rule", created_at: "2026-05-01T00:00:00Z" },
      { id: 3, name: "4K", source: "sonarr", created_at: "2026-05-01T00:00:00Z" },
    ]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText("Tags")).toBeInTheDocument();
    });
    // Source headers in their human-friendly forms.
    expect(screen.getByText("Manual")).toBeInTheDocument();
    expect(screen.getByText("From rules")).toBeInTheDocument();
    expect(screen.getByText("From Sonarr")).toBeInTheDocument();
    // Tag names render as chips.
    expect(screen.getByText("watched")).toBeInTheDocument();
    expect(screen.getByText("needs-review")).toBeInTheDocument();
    expect(screen.getByText("4K")).toBeInTheDocument();
  });

  it("OMITS the Tags section entirely when the file has no tags", async () => {
    mockGets([]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    // Wait for the drawer to settle.
    await waitFor(() => {
      expect(screen.getByText("Matched rules")).toBeInTheDocument();
    });
    // Per the audit's pattern, no perpetual empty state.
    expect(screen.queryByText("Tags")).toBeNull();
  });

  it("preserves casing — Sonarr '4K' and rule '4k' render distinctly", async () => {
    mockGets([
      { id: 1, name: "4K", source: "sonarr", created_at: "2026-05-01T00:00:00Z" },
      { id: 2, name: "4k", source: "rule", created_at: "2026-05-01T00:00:00Z" },
    ]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText("Tags")).toBeInTheDocument();
    });
    // Both casings present as separate chips.
    expect(screen.getByText("4K")).toBeInTheDocument();
    expect(screen.getByText("4k")).toBeInTheDocument();
  });

  it("falls back to a capitalized label for unknown sources", async () => {
    mockGets([
      { id: 1, name: "from-trakt", source: "trakt", created_at: "2026-05-01T00:00:00Z" },
    ]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText("Tags")).toBeInTheDocument();
    });
    expect(screen.getByText("From Trakt")).toBeInTheDocument();
  });
});
