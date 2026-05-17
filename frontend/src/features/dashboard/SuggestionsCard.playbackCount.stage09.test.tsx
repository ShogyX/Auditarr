/**
 * Stage 09 (v1.7) — SuggestionsCard playback-count fix test.
 *
 * Addendum A.10 contract:
 *     Insert 25 playback events into a test DB, run the
 *     analyzer, render SuggestionsCard with useRuleSuggestions
 *     returning empty, assert the displayed count in the
 *     empty-state text is 25 (or however many events exist),
 *     not 0.
 *
 * The bug-pattern: an operator with broken path mappings has
 * 25 actual playbacks but 0 of them link to MediaFiles. The
 * card previously read ``examined_events`` (resolved-only) and
 * showed "0 playbacks in the last 30 days". Stage 09 makes the
 * card read ``examined_events_total`` so the operator sees the
 * true count.
 *
 * This file also pins addendum A.7: when ``unresolved > 0`` the
 * card renders a path-mappings hint linking to Integrations.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: async (path: string) => {
      if (path === "/rules/suggestions") return [];
      return null;
    },
    post: async (_path: string, _body?: unknown) => {
      throw new Error("post not stubbed by default");
    },
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
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

// Mock the useRunAnalyzer hook to surface the new count fields
// without going through the full mutation lifecycle. We intercept
// the module after the apiClient mock so the imports resolve
// correctly.
vi.mock("@/hooks/useRules", async () => {
  const actual: Record<string, unknown> = await vi.importActual(
    "@/hooks/useRules",
  );
  return {
    ...actual,
    useRuleSuggestions: () => ({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    }),
    useRunAnalyzer: () => ({
      data: __nextAnalyzerOutcome,
      mutate: vi.fn(),
      isPending: false,
    }),
    useDeploySuggestion: () => ({ mutate: vi.fn(), isPending: false }),
    useDismissSuggestion: () => ({ mutate: vi.fn(), isPending: false }),
  };
});

// Module-level variable tweaked per test before render.
let __nextAnalyzerOutcome:
  | {
      examined_events: number;
      candidates_generated: number;
      suggestions_created: number;
      skipped_deduped: number;
      skipped_dismissed: number;
      skipped_deployed: number;
      skipped_too_few_events: boolean;
      examined_events_total?: number;
      examined_events_resolved?: number;
      examined_events_unresolved?: number;
    }
  | undefined;

import { SuggestionsCard } from "@/features/dashboard/SuggestionsCard";

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

// ── Test 1 — Addendum A.10 contract (bug-pattern scenario) ─────

describe("SuggestionsCard playback-count fix (Stage 09)", () => {
  it("shows the TRUE event count when all 25 playbacks are unresolved", async () => {
    // Bug-pattern: operator has 25 playbacks but broken path
    // mappings mean none resolve. Before Stage 09 the card
    // read ``examined_events`` and showed "0".
    __nextAnalyzerOutcome = {
      examined_events: 0, // resolved-only (legacy)
      candidates_generated: 0,
      suggestions_created: 0,
      skipped_deduped: 0,
      skipped_dismissed: 0,
      skipped_deployed: 0,
      skipped_too_few_events: true,
      examined_events_total: 25,
      examined_events_resolved: 0,
      examined_events_unresolved: 25,
    };

    render(wrap(<SuggestionsCard onReview={() => {}} />));

    await waitFor(() => {
      expect(screen.getByText(/No suggestions yet/i)).toBeInTheDocument();
    });
    // The displayed count must be 25, not 0.
    expect(
      screen.getByText(/Auditarr saw 25 playback events/i),
    ).toBeInTheDocument();
    // And NOT the buggy old wording "saw 0 playback events".
    expect(
      screen.queryByText(/Auditarr saw 0 playback events/i),
    ).not.toBeInTheDocument();
  });

  // ── Test 2 — Mixed resolved/unresolved + path-mapping hint ──

  it("renders the path-mappings hint when some events are unresolved (addendum A.7)", async () => {
    __nextAnalyzerOutcome = {
      examined_events: 15,
      candidates_generated: 0,
      suggestions_created: 0,
      skipped_deduped: 0,
      skipped_dismissed: 0,
      skipped_deployed: 0,
      skipped_too_few_events: true,
      examined_events_total: 25,
      examined_events_resolved: 15,
      examined_events_unresolved: 10,
    };

    render(wrap(<SuggestionsCard onReview={() => {}} />));
    await waitFor(() => {
      expect(screen.getByText(/Auditarr saw 25 playback events/i)).toBeInTheDocument();
    });

    const hint = screen.getByTestId(
      "suggestions-card-unresolved-hint",
    );
    expect(hint).toBeInTheDocument();
    expect(hint).toHaveTextContent(/10 of 25 playbacks couldn't be matched/);
    // Links to the Integrations page so the operator can fix it.
    const link = screen.getByRole("link", { name: /configure path mappings/i });
    expect(link).toHaveAttribute("href", "/integrations");
  });

  // ── Test 3 — No hint when all events resolved ───────────────

  it("does NOT render the path-mappings hint when all events resolve", async () => {
    __nextAnalyzerOutcome = {
      examined_events: 10,
      candidates_generated: 0,
      suggestions_created: 0,
      skipped_deduped: 0,
      skipped_dismissed: 0,
      skipped_deployed: 0,
      skipped_too_few_events: true,
      examined_events_total: 10,
      examined_events_resolved: 10,
      examined_events_unresolved: 0,
    };

    render(wrap(<SuggestionsCard onReview={() => {}} />));
    await waitFor(() => {
      expect(screen.getByText(/Auditarr saw 10 playback events/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("suggestions-card-unresolved-hint"),
    ).not.toBeInTheDocument();
  });

  // ── Test 4 — Backwards compatibility with older backends ────

  it("falls back to examined_events when the new split fields are absent", async () => {
    // Simulates a frontend talking to a pre-Stage-09 backend
    // that hasn't shipped the new fields. The card still shows
    // *something* (the resolved-only count) rather than blanking.
    __nextAnalyzerOutcome = {
      examined_events: 7,
      candidates_generated: 0,
      suggestions_created: 0,
      skipped_deduped: 0,
      skipped_dismissed: 0,
      skipped_deployed: 0,
      skipped_too_few_events: true,
      // examined_events_total / _resolved / _unresolved omitted.
    };

    render(wrap(<SuggestionsCard onReview={() => {}} />));
    await waitFor(() => {
      expect(screen.getByText(/Auditarr saw 7 playback events/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("suggestions-card-unresolved-hint"),
    ).not.toBeInTheDocument();
  });
});
