/**
 * v1.9 Stage 9.3 — AI badge on SuggestionsCard.
 *
 * Pins: a RuleSuggestion whose ``heuristic`` starts with
 * ``ai_`` renders an additional "AI" badge alongside the
 * heuristic-label pill. Heuristic-only suggestions don't.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: async (path: string) => {
      if (path === "/rules/suggestions") {
        return [
          {
            id: "s-1",
            name: "AI: Tag fat HEVC",
            definition: {
              match: {
                field: "video_codec",
                op: "eq",
                value: "hevc",
              },
              actions: [{ type: "add_tag", tag: "ai-flagged" }],
            },
            heuristic: "ai_openai",
            evidence: { rationale: "common transcode source" },
            files_affected: 0,
            est_runtime_s: null,
            confidence: 0.5,
            dedup_key: "ai:openai:1",
            status: "pending",
            created_at: "2026-05-18T08:00:00+00:00",
          },
          {
            id: "s-2",
            name: "Heuristic: high transcode codec",
            definition: {
              match: {
                field: "video_codec",
                op: "eq",
                value: "hevc",
              },
              actions: [{ type: "set_severity", severity: "warn" }],
            },
            heuristic: "high_transcode_codec",
            evidence: {},
            files_affected: 12,
            est_runtime_s: null,
            confidence: 0.7,
            dedup_key: "heuristic:1",
            status: "pending",
            created_at: "2026-05-18T08:00:00+00:00",
          },
        ];
      }
      if (path.startsWith("/playback/analysis-status")) {
        return {
          examined_events: 0,
          examined_events_total: 100,
          examined_events_resolved: 0,
          examined_events_unresolved: 100,
        };
      }
      return null;
    },
    post: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
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
    setTokens: vi.fn(),
    setSession: vi.fn(),
    setUser: vi.fn(),
    clear: vi.fn(),
    hydrate: vi.fn(),
  };
  return {
    useAuthStore: Object.assign((selector?: (s: typeof state) => unknown) =>
      selector ? selector(state) : state,
      {
        getState: () => state,
        setState: vi.fn(),
      },
    ),
  };
});

import { SuggestionsCard } from "@/features/dashboard/SuggestionsCard";

function withProviders(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("SuggestionsCard — AI badge (v1.9 Stage 9.3)", () => {
  it("renders an AI badge only on ai_-heuristic suggestions", async () => {
    render(withProviders(<SuggestionsCard onReview={() => {}} />));
    await waitFor(() =>
      expect(screen.getByText("AI: Tag fat HEVC")).toBeInTheDocument(),
    );
    // The AI suggestion has the badge.
    const badges = screen.getAllByTestId("ai-suggestion-badge");
    // Exactly one badge total (heuristic-only suggestion doesn't
    // get one).
    expect(badges).toHaveLength(1);
    expect(badges[0]?.textContent).toBe("AI");
  });
});
