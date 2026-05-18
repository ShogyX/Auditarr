/**
 * v1.9 Stage 9.5.6 (OP-7) — Categories card upgrades.
 *
 * Pins:
 *   1. Median bitrate matrix is sortable. Click a header to set
 *      the sort key; click again to flip direction.
 *   2. Default sort is "median desc" — operators see the
 *      heaviest-encoded buckets first without configuration.
 *   3. Bitrate cells render BOTH Mbps (primary) and kbps
 *      (muted secondary) so operators familiar with either
 *      unit can read at a glance.
 *   4. Each matrix row is a deep-link to /files?video_codec=...&container=...
 *      so operators can drill straight from "this bucket looks
 *      heavy" to the file list it represents.
 *
 * These were operator-reported as missing in the v1.9 audit
 * pass under OP-7. The CategoriesCard already shipped the
 * behavior in code; this file pins it so a future regression
 * is caught at CI time.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CategoriesCard } from "./CategoriesCard";

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardComposition: () => ({
    isLoading: false,
    isError: false,
    error: undefined,
    data: {
      resolutions: [
        {
          key: "1080p",
          label: "1080p",
          count: 100,
          total_size_bytes: 500_000_000_000,
        },
      ],
      extensions: [],
      containers: [],
      subtitle_formats: [],
      subtitle_languages: [],
      audio_languages: [],
      unknown_tracks: { video_unknown_count: 0, audio_unknown_count: 0 },
      subtitles_internal_external: { internal: 0, external: 0 },
      orphan_count: 0,
      bitrate_matrix: [
        {
          library_name: "Movies",
          resolution_key: "1080p",
          video_codec: "h264",
          container: "mkv",
          file_count: 100,
          median_bitrate_kbps: 8000,
        },
        {
          library_name: "Movies",
          resolution_key: "2160p",
          video_codec: "hevc",
          container: "mkv",
          file_count: 30,
          median_bitrate_kbps: 25000,
        },
        {
          library_name: "Movies",
          resolution_key: "720p",
          video_codec: "h264",
          container: "mp4",
          file_count: 50,
          median_bitrate_kbps: 4000,
        },
      ],
    },
  }),
}));

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CategoriesCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CategoriesCard — v1.9 Stage 9.5.6 (OP-7) bitrate matrix", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders both Mbps (primary) and kbps (secondary) for each row", () => {
    renderCard();
    // 25000 kbps → 25.0 Mbps + (25,000 kbps).
    expect(screen.getByText(/25\.0 Mbps/)).toBeInTheDocument();
    expect(screen.getByText(/25,000 kbps/)).toBeInTheDocument();
    // 4000 kbps → 4.0 Mbps + (4,000 kbps).
    expect(screen.getByText(/4\.0 Mbps/)).toBeInTheDocument();
    expect(screen.getByText(/4,000 kbps/)).toBeInTheDocument();
  });

  it("defaults to median bitrate descending", () => {
    renderCard();
    const rows = screen.getAllByTestId(/bitrate-row-link-/);
    // 25k (2160p hevc), 8k (1080p h264), 4k (720p h264) — descending.
    expect(rows[0]).toHaveAttribute("data-testid", "bitrate-row-link-2160p");
    expect(rows[1]).toHaveAttribute("data-testid", "bitrate-row-link-1080p");
    expect(rows[2]).toHaveAttribute("data-testid", "bitrate-row-link-720p");
  });

  it("flips sort direction when the same header is clicked twice", () => {
    renderCard();
    // Default is desc; one click flips to asc.
    fireEvent.click(screen.getByTestId("bitrate-sort-median"));
    const rowsAsc = screen.getAllByTestId(/bitrate-row-link-/);
    expect(rowsAsc[0]).toHaveAttribute("data-testid", "bitrate-row-link-720p");
    expect(rowsAsc[2]).toHaveAttribute("data-testid", "bitrate-row-link-2160p");
    // Re-query the header — the click triggers a re-render that
    // produces a fresh DOM node with the updated aria-sort.
    expect(screen.getByTestId("bitrate-sort-median")).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("sorts by files count when the Files column header is clicked", () => {
    renderCard();
    fireEvent.click(screen.getByTestId("bitrate-sort-files"));
    // First click on numeric column → desc; 100, 50, 30.
    const rows = screen.getAllByTestId(/bitrate-row-link-/);
    expect(rows[0]).toHaveAttribute("data-testid", "bitrate-row-link-1080p");
    expect(rows[1]).toHaveAttribute("data-testid", "bitrate-row-link-720p");
    expect(rows[2]).toHaveAttribute("data-testid", "bitrate-row-link-2160p");
    expect(screen.getByTestId("bitrate-sort-files")).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("each row carries a deep-link to /files with codec + container filters", () => {
    renderCard();
    const row = screen.getByTestId("bitrate-row-link-2160p");
    expect(row).toHaveAttribute(
      "data-href",
      "/files?video_codec=hevc&container=mkv",
    );
  });
});
