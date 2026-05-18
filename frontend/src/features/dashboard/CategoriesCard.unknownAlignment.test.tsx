/**
 * v1.9 Stage 3.3 — supersedes the Stage 04 unknown-row alignment
 * tests that lived in this file.
 *
 * The bar-graph DOM that the original tests pinned
 * (``.categories-row-link`` vs ``.categories-row-static`` grid
 * alignment, ``unknown`` row sharing a grid template) no longer
 * exists — Stage 3.3 replaced the Categories card with a
 * structured panel of independent sections (Resolutions, Top
 * extensions, Containers, Subtitle/Audio languages, Unknown
 * tracks, Orphan count, Median bitrate matrix). There is no
 * common grid template across sections any more, and "unknown"
 * is no longer a special row variant — it's either a regular
 * resolution bucket (when ``height`` is NULL on probed media) or
 * a dedicated "Unknown tracks" section (counting probed files
 * with NULL video/audio codec).
 *
 * We keep one small smoke test so the file's purpose stays
 * tracked: render the card with a representative composition
 * payload and assert it doesn't throw and the new sections show
 * up.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CategoriesCard } from "./CategoriesCard";

// Stub the v1.9 composition hook. The card no longer reads from
// ``useDashboardCategories``; it pulls a full structured payload
// from ``useDashboardComposition``.
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
      extensions: [
        { key: "mkv", label: "mkv", count: 80, total_size_bytes: 400e9 },
      ],
      containers: [
        { key: "mkv", label: "MKV", count: 80, total_size_bytes: 400e9 },
      ],
      subtitle_formats: [],
      subtitle_languages: [],
      audio_languages: [],
      unknown_tracks: { video_unknown_count: 0, audio_unknown_count: 0 },
      subtitles_internal_external: { internal: 0, external: 0 },
      orphan_count: 0,
      bitrate_matrix: [],
    },
  }),
}));

function renderCategoriesCard() {
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

describe("CategoriesCard — v1.9 Stage 3.3 smoke", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders without throwing and surfaces the new section headings", () => {
    renderCategoriesCard();
    expect(screen.getByText("Resolutions")).toBeInTheDocument();
    expect(screen.getByText("Containers")).toBeInTheDocument();
  });
});
