/**
 * Stage 14 (plan §636) — Help page docs search.
 *
 * Pins that typing into the search box hits the
 * ``/docs/search`` endpoint and renders the returned hits.
 *
 * The search input and result rendering are pre-existing
 * (DocsNav swaps to a SearchResults panel when ``query``
 * is non-empty). This test pins the behaviour so future
 * refactors don't accidentally drop the search hookup.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// Mock the API client BEFORE importing the page.
const __mockCategories = {
  rules: [
    {
      id: "rules/ai-authoring",
      title: "Writing rules with an AI assistant",
      category: "rules",
      summary: "How to draft rules with an AI.",
      tags: ["rules", "ai"],
      help_contexts: ["rules.ai-authoring"],
    },
  ],
};

const __mockSearch = [
  {
    page_id: "rules/ai-authoring",
    title: "Writing rules with an AI assistant",
    category: "rules",
    score: 1.5,
    excerpt: "How to draft Auditarr rules with an AI assistant.",
  },
];

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: vi.fn(async (path: string) => {
      if (path === "/docs/categories") return __mockCategories;
      if (path.startsWith("/docs/search")) return __mockSearch;
      if (path.startsWith("/docs/")) {
        return {
          id: "rules/ai-authoring",
          title: "Writing rules with an AI assistant",
          category: "rules",
          summary: "How to draft rules with an AI.",
          tags: [],
          help_contexts: [],
          related: [],
          body_html: "<p>Body.</p>",
        };
      }
      return {};
    }),
  },
  ApiError: class ApiError extends Error {},
}));

vi.mock("@/hooks/useHelpKey", () => ({
  useHelpKey: () => undefined,
}));

import { HelpPage } from "@/features/help/HelpPage";

function wrap(node: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Stage 14 — HelpPage docs search", () => {
  it("renders the search input with the expected placeholder", async () => {
    render(wrap(<HelpPage />));
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/search docs/i),
      ).toBeInTheDocument();
    });
  });

  it("typing into the search field renders search hits", async () => {
    render(wrap(<HelpPage />));

    const search = await screen.findByPlaceholderText(/search docs/i);
    fireEvent.change(search, { target: { value: "AI assistant" } });

    // The mocked search result should appear.
    await waitFor(() => {
      // The hit's title appears as the search-result row.
      const matches = screen.getAllByText(
        /Writing rules with an AI assistant/i,
      );
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it("clearing the search restores the category tree", async () => {
    render(wrap(<HelpPage />));

    const search = await screen.findByPlaceholderText(/search docs/i);
    fireEvent.change(search, { target: { value: "AI" } });
    await waitFor(() => {
      expect(
        screen.getAllByText(/Writing rules/i).length,
      ).toBeGreaterThan(0);
    });

    fireEvent.change(search, { target: { value: "" } });
    // The category header re-appears (rendered by CategoryTree).
    await waitFor(() => {
      // CategoryTree renders category names; "rules" should show.
      const headers = screen.getAllByText(/rules/i);
      expect(headers.length).toBeGreaterThan(0);
    });
  });
});
