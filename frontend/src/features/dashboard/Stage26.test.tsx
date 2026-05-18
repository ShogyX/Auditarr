/**
 * Stage 26 — Dashboard modernization tests.
 *
 * Pins the operational contracts of the Stage 26 additions:
 *
 *   - CategoriesCard fetches /dashboard/categories and renders
 *     section headers per group (video_codec / container)
 *   - "unknown" rows render with the demoted styling
 *   - Empty composition shows the empty state without crashing
 *   - RangeToggle issues a different series query when changed
 *   - Library row links carry the ``?library_id=`` query param
 *   - Recent scans / jobs render in the .files-table layout
 *
 * The legacy SuggestionsCard / SuggestionReviewModal and overview
 * tile semantics aren't re-exercised here — those were stable
 * pre-Stage-26 and live in their own test files.
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

import { CategoriesCard } from "@/features/dashboard/CategoriesCard";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { RangeToggle } from "@/features/dashboard/RangeToggle";

// ── Fixtures ──────────────────────────────────────────────────
const OVERVIEW = {
  file_count: 100,
  library_count: 2,
  integration_count: 1,
  integration_ok_count: 1,
  rule_count: 5,
  rule_enabled_count: 4,
  severity_counts: {
    ok: 70,
    info: 10,
    warn: 10,
    high: 5,
    error: 3,
    crit: 2,
    total: 100,
  },
  issues_open: 30,
  optimization_counts: { queued: 0, running: 0, completed: 0, failed: 0 },
  last_scan_at: "2026-05-10T12:00:00Z",
  total_size_bytes: 16 * 1024 * 1024 * 1024,
};

const SERIES_30D = {
  days: 30,
  issues_opened: Array.from({ length: 30 }, (_, i) => 30 - i),
  issues_resolved: Array.from({ length: 30 }, (_, i) => i),
  integrity_score: Array.from({ length: 30 }, (_, i) => 90 + i * 0.1),
  files_seen: Array.from({ length: 30 }, () => 100),
};

const SERIES_7D = {
  days: 7,
  issues_opened: [5, 4, 3, 2, 1, 0, 1],
  issues_resolved: [0, 1, 2, 3, 4, 5, 4],
  integrity_score: [90, 91, 92, 93, 94, 95, 96],
  files_seen: [100, 100, 100, 100, 100, 100, 100],
};

const LIBRARIES = [
  {
    library_id: "lib-aaa",
    library_name: "Movies",
    file_count: 60,
    severity: {
      ok: 50,
      info: 5,
      warn: 3,
      high: 1,
      error: 1,
      crit: 0,
      total: 60,
    },
  },
  {
    library_id: "lib-bbb",
    library_name: "Shows",
    file_count: 40,
    severity: {
      ok: 30,
      info: 5,
      warn: 3,
      high: 1,
      error: 1,
      crit: 0,
      total: 40,
    },
  },
];

const CATEGORIES = [
  {
    key: "hevc",
    label: "hevc",
    group: "video_codec",
    file_count: 60,
    total_size_bytes: 10 * 1024 * 1024 * 1024,
  },
  {
    key: "h264",
    label: "h264",
    group: "video_codec",
    file_count: 30,
    total_size_bytes: 5 * 1024 * 1024 * 1024,
  },
  {
    key: "av1",
    label: "av1",
    group: "video_codec",
    file_count: 10,
    total_size_bytes: 1 * 1024 * 1024 * 1024,
  },
  {
    key: "matroska",
    label: "matroska",
    group: "container",
    file_count: 70,
    total_size_bytes: 12 * 1024 * 1024 * 1024,
  },
  {
    key: "mp4",
    label: "mp4",
    group: "container",
    file_count: 25,
    total_size_bytes: 3 * 1024 * 1024 * 1024,
  },
  {
    key: "unknown",
    label: "unknown",
    group: "container",
    file_count: 5,
    total_size_bytes: 1 * 1024 * 1024 * 1024,
  },
];

// v1.9 Stage 3.3 — the CategoriesCard now fetches a structured
// composition payload from /dashboard/composition. The old
// /dashboard/categories endpoint is still reachable but no longer
// drives the card. We mock both so any in-flight DashboardPage
// queries continue to resolve.
const COMPOSITION = {
  resolutions: [
    { key: "1080p", label: "1080p", count: 60, total_size_bytes: 10 * 1024 * 1024 * 1024 },
    { key: "720p", label: "720p", count: 30, total_size_bytes: 5 * 1024 * 1024 * 1024 },
    { key: "4k", label: "4K", count: 10, total_size_bytes: 1 * 1024 * 1024 * 1024 },
  ],
  extensions: [
    { key: "mkv", label: "mkv", count: 70, total_size_bytes: 12 * 1024 * 1024 * 1024 },
    { key: "mp4", label: "mp4", count: 30, total_size_bytes: 4 * 1024 * 1024 * 1024 },
  ],
  containers: [
    { key: "mkv", label: "MKV", count: 70, total_size_bytes: 12 * 1024 * 1024 * 1024 },
    { key: "mp4", label: "MP4", count: 25, total_size_bytes: 3 * 1024 * 1024 * 1024 },
  ],
  subtitle_formats: [
    { key: "subrip", label: "SRT", count: 40, total_size_bytes: 0 },
  ],
  subtitle_languages: [
    { key: "en", label: "en", count: 50, total_size_bytes: 0 },
    { key: "es", label: "es", count: 20, total_size_bytes: 0 },
  ],
  audio_languages: [
    { key: "en", label: "en", count: 80, total_size_bytes: 0 },
  ],
  unknown_tracks: { video_unknown_count: 0, audio_unknown_count: 2 },
  subtitles_internal_external: { internal: 40, external: 15 },
  orphan_count: 3,
  bitrate_matrix: [
    {
      library_id: "lib-aaa",
      library_name: "Movies",
      resolution_key: "1080p",
      video_codec: "h264",
      container: "MKV",
      file_count: 60,
      median_bitrate_kbps: 5000,
    },
  ],
};

const COMPOSITION_EMPTY = {
  resolutions: [],
  extensions: [],
  containers: [],
  subtitle_formats: [],
  subtitle_languages: [],
  audio_languages: [],
  unknown_tracks: { video_unknown_count: 0, audio_unknown_count: 0 },
  subtitles_internal_external: { internal: 0, external: 0 },
  orphan_count: 0,
  bitrate_matrix: [],
};

const RECENT_SCAN = {
  id: "scan-aaa",
  library_id: "lib-aaa",
  library_name: "Movies",
  mode: "full",
  status: "completed",
  files_seen: 60,
  started_at: "2026-05-12T10:00:00Z",
  finished_at: "2026-05-12T10:05:00Z",
};

const RECENT_JOB = {
  id: "job-aaa",
  job_kind: "scan",
  status: "completed",
  trigger: "schedule",
  started_at: "2026-05-12T10:00:00Z",
  duration_ms: 5000,
  error: null,
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
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/dashboard/overview") return OVERVIEW;
    if (path.startsWith("/dashboard/series?days=7")) return SERIES_7D;
    if (path.startsWith("/dashboard/series")) return SERIES_30D;
    if (path === "/dashboard/libraries") return LIBRARIES;
    if (path === "/dashboard/integrations") return [];
    if (path.startsWith("/dashboard/top-rules")) return [];
    if (path.startsWith("/dashboard/recent-scans")) return [RECENT_SCAN];
    if (path.startsWith("/dashboard/recent-job-runs")) return [RECENT_JOB];
    if (path.startsWith("/dashboard/categories")) return CATEGORIES;
    if (path.startsWith("/dashboard/composition")) return COMPOSITION;
    if (path === "/dashboard/sidebar-badges") {
      return { issuesOpen: 30, rulesEnabled: 4, activeOptimizations: 0 };
    }
    if (path === "/rules/suggestions") return [];
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── CategoriesCard ───────────────────────────────────────────

describe("CategoriesCard", () => {
  it("renders the v1.9 Stage 3.3 structured sections", async () => {
    // The card now ships ten sections from /dashboard/composition
    // rather than two bar-graph groups. Pin the sections an operator
    // would expect to see for a populated library — anything visible
    // here is a stable contract that downstream test selectors can
    // rely on.
    render(wrap(<CategoriesCard />));
    await screen.findByText("Resolutions");
    expect(screen.getByText("Containers")).toBeInTheDocument();
    expect(screen.getByText("Top extensions")).toBeInTheDocument();
    expect(screen.getByText("Subtitle languages")).toBeInTheDocument();
    expect(screen.getByText("Audio languages")).toBeInTheDocument();

    // Resolution rows surface bucket labels (1080p, 720p, 4K).
    // The label "1080p" also appears in the bitrate-matrix row,
    // so we assert ≥1 match rather than getByText (which insists
    // on exactly one).
    expect(screen.getAllByText("1080p").length).toBeGreaterThan(0);
    expect(screen.getByText("720p")).toBeInTheDocument();
    expect(screen.getByText("4K")).toBeInTheDocument();

    // Container rows surface NORMALIZED labels — MKV / MP4, not
    // the raw matroska / mp4 / mov demuxer aliases that the
    // pre-3.3 card displayed verbatim. "MKV" appears in both the
    // Containers section and the bitrate-matrix row.
    expect(screen.getAllByText("MKV").length).toBeGreaterThan(0);
    expect(screen.getByText("MP4")).toBeInTheDocument();
  });

  it("surfaces unknown-track and orphan counts when non-zero", async () => {
    // The pre-1.9 "unknown" row badge moved to a dedicated
    // section ("Unknown tracks") in Stage 3.3. The unknown-count
    // signal still surfaces — just in a different DOM shape — so
    // an operator with probe stragglers still sees them.
    render(wrap(<CategoriesCard />));
    await screen.findByText("Unknown tracks");
    expect(screen.getByText(/no audio codec/i)).toBeInTheDocument();
    // Orphan section appears whenever orphan_count > 0.
    expect(screen.getByText("Orphan files")).toBeInTheDocument();
  });

  it("renders empty state when the composition is empty", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/dashboard/composition")) return COMPOSITION_EMPTY;
      return null;
    });
    render(wrap(<CategoriesCard />));
    // Stage 3.3 changed the empty-state copy to talk about
    // "media" (was "files") since sidecar files are now scoped
    // out of the composition payload entirely.
    await screen.findByText(/no media yet/i);
  });

  it("renders error state when the request fails", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/dashboard/composition")) {
        throw new Error("oops");
      }
      return null;
    });
    render(wrap(<CategoriesCard />));
    await screen.findByText(/failed to load composition/i);
  });
});

// ── RangeToggle ──────────────────────────────────────────────

describe("RangeToggle", () => {
  it("renders three options and reports the active one via aria-checked", () => {
    const onChange = vi.fn();
    render(<RangeToggle value={30} onChange={onChange} />);
    const opt7 = screen.getByRole("radio", { name: "7d" });
    const opt30 = screen.getByRole("radio", { name: "30d" });
    const opt90 = screen.getByRole("radio", { name: "90d" });
    expect(opt7).toHaveAttribute("aria-checked", "false");
    expect(opt30).toHaveAttribute("aria-checked", "true");
    expect(opt90).toHaveAttribute("aria-checked", "false");
  });

  it("calls onChange when an option is clicked", () => {
    const onChange = vi.fn();
    render(<RangeToggle value={30} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "7d" }));
    expect(onChange).toHaveBeenCalledWith(7);
  });
});

// ── DashboardPage integration ────────────────────────────────

describe("DashboardPage Stage 26 wiring", () => {
  it("library rows link with ?library_id=<id>", async () => {
    render(wrap(<DashboardPage />));
    // "Movies" appears in both the Library card and the Recent scans
    // table; wait for both to render rather than for a single match.
    await waitFor(() =>
      expect(screen.getAllByText("Movies").length).toBeGreaterThan(0),
    );

    // The library-row anchor lives inside the "Libraries" card. We
    // find it by the unique combination of name + file-count tag.
    const filesTag = screen.getByText(/60 files/i);
    const moviesLink = filesTag.closest("a");
    expect(moviesLink).toBeTruthy();
    expect(moviesLink?.getAttribute("href")).toContain("library_id=lib-aaa");
  });

  it("range toggle in header switches the series query", async () => {
    render(wrap(<DashboardPage />));
    await waitFor(() =>
      expect(screen.getAllByText("Movies").length).toBeGreaterThan(0),
    );

    // First load fetched /dashboard/series?days=30.
    expect(
      apiGet.mock.calls.some(
        ([p]) =>
          typeof p === "string" && p.startsWith("/dashboard/series?days=30"),
      ),
    ).toBe(true);

    // Switch to 7d.
    fireEvent.click(screen.getByRole("radio", { name: "7d" }));

    await waitFor(() => {
      expect(
        apiGet.mock.calls.some(
          ([p]) =>
            typeof p === "string" && p.startsWith("/dashboard/series?days=7"),
        ),
      ).toBe(true);
    });
  });

  it("recent scans render as a table row", async () => {
    render(wrap(<DashboardPage />));
    await waitFor(() =>
      expect(screen.getAllByText("Movies").length).toBeGreaterThan(0),
    );

    // Find the grid that carries the scan-specific cells.
    const grids = await screen.findAllByRole("grid");
    const scansGrid = grids.find((g) => within(g).queryByText("full"));
    expect(scansGrid).toBeTruthy();
    expect(within(scansGrid!).getByText("completed")).toBeInTheDocument();
  });

  it("recent automation jobs render with duration formatted", async () => {
    render(wrap(<DashboardPage />));
    await waitFor(() =>
      expect(screen.getAllByText("Movies").length).toBeGreaterThan(0),
    );

    // RECENT_JOB.duration_ms = 5000 → "5.0s"
    expect(screen.getByText(/5\.0s/)).toBeInTheDocument();
  });
});
