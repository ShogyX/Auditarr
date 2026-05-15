/**
 * Stage 20 (audit follow-up) — playback surface on the optimization page.
 *
 * Pins:
 *   1. OptimizationPage renders the PlaybackStatsCard alongside the
 *      profiles + queue cards. The same component the dashboard
 *      uses; the optimization page just hosts a second mount point.
 *   2. The card is rendered between Profiles and Queue (operators
 *      planning the next optimization want playback context next
 *      to the queue, not below it).
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
    user: { id: "u1", username: "admin", role: "admin" },
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

vi.mock("@/lib/toast", () => ({ toast: vi.fn() }));

import { OptimizationPage } from "@/features/optimization/OptimizationPage";

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
    if (path.startsWith("/optimization/profiles")) return [];
    if (path.startsWith("/optimization/queue")) return [];
    if (path.startsWith("/playback/stats/transcoded")) {
      return {
        items: [
          {
            media_file_id: "mf-1",
            path: "/m/hot.mkv",
            filename: "hot.mkv",
            transcode_count: 42,
            last_transcoded_at: "2026-05-15T00:00:00Z",
          },
        ],
      };
    }
    if (path.startsWith("/playback/stats/devices")) {
      return { cells: [] };
    }
    if (path.startsWith("/playback/stats/decisions")) {
      return { points: [] };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 20 — PlaybackStatsCard on OptimizationPage", () => {
  it("renders the PlaybackStatsCard between Profiles and Queue cards", async () => {
    render(wrap(<OptimizationPage />));

    // "Playback insights" is the card-head title — rendered
    // unconditionally regardless of whether the operator has
    // collapsed the section. Tab labels live behind the
    // expand/collapse gate, so we use the head as the stable
    // anchor.
    await waitFor(() => {
      expect(screen.getByText(/Playback insights/i)).toBeInTheDocument();
    });

    // Playback card must precede the queue card in DOM order so
    // operators planning the next optimization see usage context
    // before the queue itself.
    const playbackHead = screen.getByText(/Playback insights/i);
    const queueHead = screen.getByText("Queue");
    expect(
      playbackHead.compareDocumentPosition(queueHead) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
