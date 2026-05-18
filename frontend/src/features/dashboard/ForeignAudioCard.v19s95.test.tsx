/**
 * v1.9 Stage 9.5.7 (OP-8) — ForeignAudioCard contract.
 *
 * Pins:
 *   1. count > 0 + configured → renders count + view link
 *   2. count == 0 + configured → hides entirely
 *   3. count == 0 + unconfigured (empty preference lists) →
 *      renders the configure-link nudge
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ForeignAudioCard } from "./ForeignAudioCard";

const hookMock = vi.fn();

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardForeignAudio: () => hookMock(),
}));

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ForeignAudioCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ForeignAudioCard — v1.9 Stage 9.5.7 (OP-8)", () => {
  beforeEach(() => {
    hookMock.mockReset();
  });

  it("renders the count and view link when count > 0", () => {
    hookMock.mockReturnValue({
      isLoading: false,
      data: {
        count: 42,
        sample_ids: ["a", "b"],
        preferred_audio_languages: ["eng"],
        preferred_subtitle_languages: ["eng"],
      },
    });
    renderCard();
    expect(screen.getByTestId("foreign-audio-card")).toBeInTheDocument();
    expect(screen.getByTestId("foreign-audio-view-link")).toHaveAttribute(
      "href",
      "/files?tag=foreign-audio-no-subs",
    );
    expect(screen.getByText(/42/)).toBeInTheDocument();
  });

  it("hides entirely when count is 0 and preferences are configured", () => {
    hookMock.mockReturnValue({
      isLoading: false,
      data: {
        count: 0,
        sample_ids: [],
        preferred_audio_languages: ["eng"],
        preferred_subtitle_languages: ["eng"],
      },
    });
    const { container } = renderCard();
    expect(container.firstChild).toBeNull();
  });

  it("renders the configure nudge when preferences are empty", () => {
    hookMock.mockReturnValue({
      isLoading: false,
      data: {
        count: 0,
        sample_ids: [],
        preferred_audio_languages: [],
        preferred_subtitle_languages: [],
      },
    });
    renderCard();
    expect(screen.getByTestId("foreign-audio-configure-link")).toHaveAttribute(
      "href",
      "/settings",
    );
  });
});
