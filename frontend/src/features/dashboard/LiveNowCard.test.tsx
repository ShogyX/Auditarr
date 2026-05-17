/**
 * Stage 09 (v1.7) — LiveNowCard tile rendering.
 *
 * Covers:
 *   - Loading state while the poll is in flight.
 *   - Empty state when no sessions are active.
 *   - Sessions rendered with title + meta + decision pill +
 *     progress bar.
 *   - Path-mappings hint surfaces when ``unresolved > 0``
 *     (addendum A.7).
 *   - Resolved sessions deep-link to the file detail page.
 *
 * The hook itself is mocked so we exercise the rendering
 * contract without going through the network.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import type { LivePlaybackResponse } from "@/hooks/usePlayback";

vi.mock("@/stores/uiStore", () => {
  const state = {
    dashboardHidden: [] as string[],
    // Stage 13 (plan §606) — sub-cards read this too.
    dashboardDisabled: [] as string[],
    toggleDashboardSection: vi.fn(),
  };
  type S = typeof state;
  const useUiStore = vi.fn((sel?: (s: S) => unknown) =>
    typeof sel === "function" ? sel(state) : state,
  );
  return { useUiStore };
});

let __nextResponse: LivePlaybackResponse | undefined;
let __nextState: "loading" | "error" | "ok" = "ok";

vi.mock("@/hooks/usePlayback", async () => {
  const actual: Record<string, unknown> = await vi.importActual(
    "@/hooks/usePlayback",
  );
  return {
    ...actual,
    useLivePlaybacks: () => {
      if (__nextState === "loading") {
        return {
          data: undefined,
          isLoading: true,
          isError: false,
          error: null,
        };
      }
      if (__nextState === "error") {
        return {
          data: undefined,
          isLoading: false,
          isError: true,
          error: new Error("boom"),
        };
      }
      return {
        data: __nextResponse,
        isLoading: false,
        isError: false,
        error: null,
      };
    },
  };
});

import { LiveNowCard } from "@/features/dashboard/LiveNowCard";

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

describe("LiveNowCard (Stage 09)", () => {
  it("renders the loading state while the first poll is in flight", () => {
    __nextState = "loading";
    render(wrap(<LiveNowCard />));
    expect(screen.getByText(/polling live sessions/i)).toBeInTheDocument();
  });

  it("renders the empty state when no sessions are active", () => {
    __nextState = "ok";
    __nextResponse = {
      sessions: [],
      total: 0,
      resolved: 0,
      unresolved: 0,
      polled_at: "2026-05-16T12:00:00Z",
    };
    render(wrap(<LiveNowCard />));
    expect(
      screen.getByText(/nothing playing right now/i),
    ).toBeInTheDocument();
  });

  it("renders one row per session with title, decision pill, and progress bar", () => {
    __nextState = "ok";
    __nextResponse = {
      sessions: [
        {
          integration_id: "ig-1",
          integration_name: "Plex",
          integration_kind: "plex",
          upstream_id: "sess-1",
          source_path: "/mnt/media/Movies/Inception.mkv",
          decision: "transcode",
          state: "playing",
          started_at: new Date(Date.now() - 5 * 60_000).toISOString(),
          progress_pct: 42.5,
          user: "alice",
          device_kind: "Roku",
          device_name: "Living Room",
          source_codec: "hevc",
          source_bitrate_kbps: 12000,
          source_width: 3840,
          source_height: 2160,
          source_container: "mkv",
          target_codec: "h264",
          target_bitrate_kbps: 8000,
          title: "Inception",
          media_file_id: "file-99",
        },
      ],
      total: 1,
      resolved: 1,
      unresolved: 0,
      polled_at: "2026-05-16T12:00:00Z",
    };
    render(wrap(<LiveNowCard />));

    // Title shows.
    expect(screen.getByText("Inception")).toBeInTheDocument();
    // The title is a deep-link to the file detail page when
    // resolved.
    const link = screen.getByRole("link", { name: /inception/i });
    expect(link).toHaveAttribute("href", "/files/file-99");
    // Decision pill rendered.
    expect(screen.getByText(/transcode/i)).toBeInTheDocument();
    // Meta line includes the user + integration.
    expect(screen.getByText(/alice/i)).toBeInTheDocument();
    expect(screen.getByText(/plex/i)).toBeInTheDocument();
    // Progress bar with the right aria semantics.
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "42.5");
  });

  it("renders the path-mappings hint when unresolved > 0 (addendum A.7)", () => {
    __nextState = "ok";
    __nextResponse = {
      sessions: [
        {
          integration_id: "ig-1",
          integration_name: "Plex",
          integration_kind: "plex",
          upstream_id: "sess-1",
          source_path: "/unknown/x.mkv",
          decision: "direct_play",
          state: "playing",
          started_at: "2026-05-16T11:55:00Z",
          progress_pct: null,
          user: null,
          device_kind: null,
          device_name: null,
          source_codec: null,
          source_bitrate_kbps: null,
          source_width: null,
          source_height: null,
          source_container: null,
          target_codec: null,
          target_bitrate_kbps: null,
          title: "unknown clip",
          media_file_id: null,
        },
      ],
      total: 1,
      resolved: 0,
      unresolved: 1,
      polled_at: "2026-05-16T12:00:00Z",
    };
    render(wrap(<LiveNowCard />));

    const hint = screen.getByTestId("live-now-unresolved-hint");
    expect(hint).toBeInTheDocument();
    expect(hint).toHaveTextContent(/1 of 1 session couldn't be matched/);
    const link = screen.getByRole("link", { name: /configure path mappings/i });
    expect(link).toHaveAttribute("href", "/integrations");

    // Unresolved title is NOT a link (no media_file_id).
    expect(
      screen.queryByRole("link", { name: /unknown clip/i }),
    ).not.toBeInTheDocument();
  });

  it("renders 'paused' indicator when a session's state is paused", () => {
    __nextState = "ok";
    __nextResponse = {
      sessions: [
        {
          integration_id: "ig-1",
          integration_name: "Plex",
          integration_kind: "plex",
          upstream_id: "sess-paused",
          source_path: "/mnt/media/Movies/x.mkv",
          decision: "direct_play",
          state: "paused",
          started_at: "2026-05-16T11:55:00Z",
          progress_pct: 30,
          user: "bob",
          device_kind: "iOS",
          device_name: null,
          source_codec: null,
          source_bitrate_kbps: null,
          source_width: null,
          source_height: null,
          source_container: null,
          target_codec: null,
          target_bitrate_kbps: null,
          title: "Pizza Night",
          media_file_id: "f1",
        },
      ],
      total: 1,
      resolved: 1,
      unresolved: 0,
      polled_at: "2026-05-16T12:00:00Z",
    };
    render(wrap(<LiveNowCard />));
    // The lowercase inline indicator.
    expect(screen.getByText(/^paused$/i)).toBeInTheDocument();
  });
});
