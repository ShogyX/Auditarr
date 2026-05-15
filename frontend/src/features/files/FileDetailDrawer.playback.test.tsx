/**
 * Stage 12 (audit follow-up) — FileDetailDrawer playback history section.
 *
 * Pins:
 *   - Drawer shows the Playback history section with rows when
 *     /playback/events returns items for this file.
 *   - Section is OMITTED entirely (no empty state) when there are
 *     no events for the file — per the Stage 12 plan, the drawer
 *     should not show a perpetual noisy empty state.
 *   - ``reason_code`` only renders for transcode / failed decisions.
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
  quarantined: false,
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
  quarantined_at: null,
  quarantined_reason: null,
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

// Helper to wire up the apiGet mock for every endpoint the drawer
// touches. ``playbackItems`` controls the playback fixture.
function mockGets(playbackItems: unknown[]) {
  apiGet.mockImplementation(async (path: string) => {
    if (path === `/media/${SUMMARY.id}`) return DETAIL;
    if (path === `/media/${SUMMARY.id}/evaluations`) return [];
    if (path.startsWith("/playback/events?")) {
      return {
        items: playbackItems,
        total: playbackItems.length,
        offset: 0,
        limit: 10,
      };
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

describe("FileDetailDrawer Stage 12 — playback history", () => {
  it("renders Playback history section when events exist", async () => {
    mockGets([
      {
        id: "pe-1",
        integration_id: "int-plex",
        integration_name: "My Plex",
        media_file_id: SUMMARY.id,
        library_id: "lib-1",
        library_name: "Movies",
        source_path: SUMMARY.path,
        device_kind: "phone",
        device_name: "iPhone",
        decision: "transcode",
        reason_code: "codec_incompat",
        source_codec: "hevc",
        source_bitrate_kbps: 12000,
        source_width: 1920,
        source_height: 1080,
        source_container: "matroska",
        target_codec: "h264",
        target_bitrate_kbps: 6000,
        started_at: "2026-05-14T12:00:00Z",
        completed_at: null,
        duration_s: null,
      },
      {
        id: "pe-2",
        integration_id: "int-jelly",
        integration_name: "My Jellyfin",
        media_file_id: SUMMARY.id,
        library_id: "lib-1",
        library_name: "Movies",
        source_path: SUMMARY.path,
        device_kind: "tv",
        device_name: "LG TV",
        decision: "direct_play",
        reason_code: null,
        source_codec: "hevc",
        source_bitrate_kbps: 12000,
        source_width: 1920,
        source_height: 1080,
        source_container: "matroska",
        target_codec: null,
        target_bitrate_kbps: null,
        started_at: "2026-05-13T18:00:00Z",
        completed_at: null,
        duration_s: null,
      },
    ]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText("Playback history")).toBeInTheDocument();
    });
    // Both decisions render as Tag pills.
    expect(screen.getByText("transcode")).toBeInTheDocument();
    expect(screen.getByText("direct_play")).toBeInTheDocument();
    // Device names render.
    expect(screen.getByText("iPhone")).toBeInTheDocument();
    expect(screen.getByText("LG TV")).toBeInTheDocument();
    // reason_code renders for the transcode event.
    expect(screen.getByText("codec_incompat")).toBeInTheDocument();
  });

  it("OMITS the Playback history section entirely when there are no events", async () => {
    mockGets([]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    // Wait for the drawer to settle (the playback query completes).
    await waitFor(() => {
      // The Matched rules section still renders — that's evidence
      // the drawer has loaded its data; we just check the playback
      // section is absent.
      expect(screen.getByText("Matched rules")).toBeInTheDocument();
    });
    // Per the Stage 12 plan, no "no playback yet" empty state.
    expect(screen.queryByText("Playback history")).toBeNull();
  });

  it("does NOT render reason_code for direct_play events", async () => {
    mockGets([
      {
        id: "pe-3",
        integration_id: "int-plex",
        integration_name: "My Plex",
        media_file_id: SUMMARY.id,
        library_id: "lib-1",
        library_name: "Movies",
        source_path: SUMMARY.path,
        device_kind: "tv",
        device_name: "LG TV",
        decision: "direct_play",
        // Poller attached a reason_code; the UI must still hide it
        // because it's noise on direct_play rows.
        reason_code: "should-not-render",
        source_codec: "h264",
        source_bitrate_kbps: 6000,
        source_width: 1920,
        source_height: 1080,
        source_container: "matroska",
        target_codec: null,
        target_bitrate_kbps: null,
        started_at: "2026-05-14T12:00:00Z",
        completed_at: null,
        duration_s: null,
      },
    ]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText("Playback history")).toBeInTheDocument();
    });
    expect(screen.queryByText("should-not-render")).toBeNull();
  });

  it("renders reason_code for failed events", async () => {
    mockGets([
      {
        id: "pe-4",
        integration_id: "int-plex",
        integration_name: "My Plex",
        media_file_id: SUMMARY.id,
        library_id: "lib-1",
        library_name: "Movies",
        source_path: SUMMARY.path,
        device_kind: "tv",
        device_name: "LG TV",
        decision: "failed",
        reason_code: "network_error",
        source_codec: "h264",
        source_bitrate_kbps: 6000,
        source_width: 1920,
        source_height: 1080,
        source_container: "matroska",
        target_codec: null,
        target_bitrate_kbps: null,
        started_at: "2026-05-14T12:00:00Z",
        completed_at: null,
        duration_s: null,
      },
    ]);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText("Playback history")).toBeInTheDocument();
    });
    expect(screen.getByText("network_error")).toBeInTheDocument();
  });
});
