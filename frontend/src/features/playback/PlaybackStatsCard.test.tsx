/**
 * Stage 12 (audit follow-up) — PlaybackStatsCard tests.
 *
 * Pins:
 *   - Three tabs render with their counts.
 *   - Switching tabs swaps the panel without crashing.
 *   - Top-transcoded panel renders rows including the unresolved bucket.
 *   - Device matrix panel renders a grid with the expected cells.
 *   - Decision trend renders one bar per day.
 *   - Empty state shows when all three queries return zero rows.
 *   - Loading state shows while transcoded query is pending.
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

// uiStore mock — dashboardHidden empty so the card body renders.
vi.mock("@/stores/uiStore", () => {
  const state = {
    dashboardHidden: [] as string[],
    toggleDashboardSection: vi.fn(),
  };
  type S = typeof state;
  const useUiStore = vi.fn((sel?: (s: S) => unknown) =>
    typeof sel === "function" ? sel(state) : state,
  );
  return { useUiStore };
});

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

/** Standard happy-path fixture: each of the three endpoints
 *  returns sample data so the card renders all three tabs. */
function mockHappyData() {
  apiGet.mockImplementation(async (path: string) => {
    if (path.startsWith("/playback/stats/transcoded")) {
      return {
        window_days: 30,
        items: [
          {
            media_file_id: "mf-1",
            path: "/data/x.mkv",
            filename: "x.mkv",
            transcode_count: 7,
            last_transcoded_at: "2026-05-14T12:00:00Z",
            source_codec: "hevc",
            target_codec: "h264",
          },
          {
            media_file_id: "mf-2",
            path: "/data/y.mkv",
            filename: "y.mkv",
            transcode_count: 3,
            last_transcoded_at: "2026-05-13T12:00:00Z",
            source_codec: "h264",
            target_codec: "h264",
          },
          {
            media_file_id: null,
            path: "<unresolved>",
            filename: null,
            transcode_count: 2,
            last_transcoded_at: "2026-05-12T12:00:00Z",
            source_codec: null,
            target_codec: null,
          },
        ],
      };
    }
    if (path.startsWith("/playback/stats/devices")) {
      return {
        window_days: 30,
        cells: [
          { device_kind: "phone", decision: "transcode", count: 5 },
          { device_kind: "tv", decision: "direct_play", count: 3 },
          { device_kind: "unknown", decision: "failed", count: 1 },
        ],
      };
    }
    if (path.startsWith("/playback/stats/decisions")) {
      return {
        window_days: 30,
        points: [
          { day: "2026-05-12", decision: "transcode", count: 2 },
          { day: "2026-05-13", decision: "transcode", count: 4 },
          { day: "2026-05-13", decision: "direct_play", count: 2 },
          { day: "2026-05-14", decision: "transcode", count: 1 },
        ],
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

describe("PlaybackStatsCard Stage 12", () => {
  it("renders all three tabs with their counts", async () => {
    mockHappyData();
    render(wrap(<PlaybackStatsCard />));

    await waitFor(() => {
      expect(
        screen.getByRole("tab", { name: /top transcoded/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("tab", { name: /device matrix/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /decision trend/i }),
    ).toBeInTheDocument();
  });

  it("Top transcoded tab renders rows including the unresolved bucket", async () => {
    mockHappyData();
    render(wrap(<PlaybackStatsCard />));

    // Default tab is "transcoded".
    await waitFor(() => {
      expect(screen.getByTestId("playback-top-transcoded-list")).toBeInTheDocument();
    });
    expect(screen.getByText("x.mkv")).toBeInTheDocument();
    expect(screen.getByText("y.mkv")).toBeInTheDocument();
    // The unresolved bucket renders as a Pill labeled "unresolved".
    expect(screen.getByText("unresolved")).toBeInTheDocument();
  });

  it("Device matrix tab renders the matrix with expected cells", async () => {
    mockHappyData();
    render(wrap(<PlaybackStatsCard />));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /device matrix/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("tab", { name: /device matrix/i }));

    await waitFor(() => {
      expect(screen.getByTestId("playback-device-matrix")).toBeInTheDocument();
    });
    // Cells have data-cell="<device>:<decision>" and data-count.
    const transcodePhone = document.querySelector(
      '[data-cell="phone:transcode"]',
    ) as HTMLElement | null;
    expect(transcodePhone).not.toBeNull();
    expect(transcodePhone!.getAttribute("data-count")).toBe("5");
    const failedUnknown = document.querySelector(
      '[data-cell="unknown:failed"]',
    ) as HTMLElement | null;
    expect(failedUnknown).not.toBeNull();
    expect(failedUnknown!.getAttribute("data-count")).toBe("1");
  });

  it("Decision trend tab renders one bar per distinct day", async () => {
    mockHappyData();
    render(wrap(<PlaybackStatsCard />));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /decision trend/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("tab", { name: /decision trend/i }));

    await waitFor(() => {
      expect(screen.getByTestId("playback-decision-trend")).toBeInTheDocument();
    });
    // Three distinct days seeded.
    const days = document.querySelectorAll("[data-day]");
    expect(days.length).toBe(3);
  });

  it("renders an empty state when all queries return zero rows", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/playback/stats/transcoded")) {
        return { window_days: 30, items: [] };
      }
      if (path.startsWith("/playback/stats/devices")) {
        return { window_days: 30, cells: [] };
      }
      if (path.startsWith("/playback/stats/decisions")) {
        return { window_days: 30, points: [] };
      }
      return null;
    });
    render(wrap(<PlaybackStatsCard />));

    await waitFor(() => {
      expect(screen.getByText(/no playback yet/i)).toBeInTheDocument();
    });
    // Tabs MUST NOT render when the empty state is shown.
    expect(
      screen.queryByRole("tab", { name: /top transcoded/i }),
    ).toBeNull();
  });

  it("shows a loading state on first fetch", () => {
    // Never resolve so the query stays pending.
    apiGet.mockImplementation(() => new Promise(() => {}));
    render(wrap(<PlaybackStatsCard />));
    expect(screen.getByText(/loading playback insights/i)).toBeInTheDocument();
  });
});
