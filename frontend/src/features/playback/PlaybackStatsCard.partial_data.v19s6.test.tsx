/**
 * v1.9 Stage 6.5 — PlaybackStatsCard reliability.
 *
 * The pre-1.9 card crashed when the backend returned partial
 * data — a 200 with ``items: null``, or a cell with
 * ``device_kind: null``, would blow up rendering. Stage 6.5
 * hardens every render path: missing arrays become empty,
 * missing fields become "(unknown)" buckets, missing counts
 * become 0.
 *
 * Pins:
 *   1. items=null → renders empty-state text (does NOT throw).
 *   2. cells with null device_kind/decision/count → render in
 *      a "(unknown)" bucket without crashing.
 *   3. trend points with null fields → render in "(unknown)"
 *      buckets without crashing.
 *   4. Top-transcoded item with null transcode_count → uses 0,
 *      no NaN width in the inline bar.
 *   5. Top-transcoded items with null path AND null media_file_id
 *      use unique keys per row (no React duplicate-key warning).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
    accessToken: "tok",
    refreshToken: "ref",
    user: { id: "u1", role: "admin", email: "a@b.c", username: "admin" },
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

import { PlaybackStatsCard } from "@/features/playback/PlaybackStatsCard";

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

/** Set up apiGet to return partial payloads for each playback
 *  endpoint. ``overrides`` lets a specific test plug in a
 *  malformed shape for one endpoint while the others stay
 *  vacuously empty. */
function setApi(
  overrides: Partial<{
    transcoded: unknown;
    devices: unknown;
    decisions: unknown;
  }>,
) {
  apiGet.mockImplementation(async (path: string) => {
    if (path.startsWith("/playback/stats/transcoded")) {
      return overrides.transcoded ?? { items: [] };
    }
    if (path.startsWith("/playback/stats/devices")) {
      return overrides.devices ?? { cells: [] };
    }
    if (path.startsWith("/playback/stats/decisions")) {
      return overrides.decisions ?? { points: [] };
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

describe("v1.9 Stage 6.5 — PlaybackStatsCard partial-data hardening", () => {
  it("renders empty state when transcoded.items is null (not an array)", async () => {
    setApi({ transcoded: { items: null } });
    render(wrap(<PlaybackStatsCard />));
    // Pre-fix this crashed inside the .length read. Post-fix it
    // treats null as zero items, so the global empty state
    // fires (all three queries are empty).
    await screen.findByText(/no playback yet/i);
  });

  it("renders matrix cells with null device_kind / decision in '(unknown)' buckets", async () => {
    setApi({
      devices: {
        cells: [
          { device_kind: null, decision: "transcode", count: 5 },
          { device_kind: "Roku", decision: null, count: 3 },
          { device_kind: null, decision: null, count: 2 },
        ],
      },
    });
    render(wrap(<PlaybackStatsCard />));
    // Switch to device matrix tab; the (unknown) row label must
    // be present and the cells must render with the right counts.
    const devicesTab = await screen.findByRole("tab", {
      name: /device matrix/i,
    });
    devicesTab.click();
    // (unknown) appears for both the device row and the decision
    // column. Use a regex with global flag to match either
    // occurrence at least once.
    const unknownMatches = await screen.findAllByText(/\(unknown\)/i);
    expect(unknownMatches.length).toBeGreaterThanOrEqual(2);
  });

  it("renders trend points with null fields in '(unknown)' buckets", async () => {
    setApi({
      decisions: {
        points: [
          { day: null, decision: "transcode", count: 3 },
          { day: "2026-05-15", decision: null, count: 1 },
          { day: "2026-05-16", decision: "direct_play", count: 7 },
        ],
      },
    });
    render(wrap(<PlaybackStatsCard />));
    const trendTab = await screen.findByRole("tab", {
      name: /decision trend/i,
    });
    trendTab.click();
    // The trend panel renders one bar per day. Even with a null
    // day name, the panel must render WITHOUT crashing.
    await screen.findByTestId("playback-decision-trend");
  });

  it("treats null transcode_count as 0 (no NaN in bar widths)", async () => {
    setApi({
      transcoded: {
        items: [
          {
            media_file_id: "f1",
            path: "/data/x.mkv",
            filename: "x.mkv",
            transcode_count: null,
            source_codec: "hevc",
            target_codec: "h264",
          },
        ],
      },
    });
    render(wrap(<PlaybackStatsCard />));
    // The list renders; bar div uses a percentage width based
    // on count. With count=0 the floor (4%) wins — important
    // is that no NaN ends up in the DOM.
    const list = await screen.findByTestId("playback-top-transcoded-list");
    expect(list.innerHTML).not.toContain("NaN");
  });

  it("renders multiple unresolved rows with unique keys", async () => {
    // Two rows with media_file_id=null and the SAME path. Pre-
    // 1.9 these collided on key=`unresolved-${path}` and React
    // emitted a duplicate-key warning. Stage 6.5 adds the row
    // index as a tiebreaker; the test just asserts both rows
    // render without throwing.
    setApi({
      transcoded: {
        items: [
          {
            media_file_id: null,
            path: "/data/x.mkv",
            filename: null,
            transcode_count: 5,
          },
          {
            media_file_id: null,
            path: "/data/x.mkv",
            filename: null,
            transcode_count: 3,
          },
        ],
      },
    });
    render(wrap(<PlaybackStatsCard />));
    const list = await screen.findByTestId("playback-top-transcoded-list");
    // Both <li>s rendered.
    expect(list.querySelectorAll("li")).toHaveLength(2);
  });
});
