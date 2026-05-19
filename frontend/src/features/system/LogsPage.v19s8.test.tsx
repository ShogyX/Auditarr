/**
 * v1.9 Stage 8.1 — LogsPage tests.
 *
 * Pins:
 *   1. Page calls GET /system/logs on mount with default filters.
 *   2. Selecting a service narrows the request URL.
 *   3. Selecting a level adds the level query param.
 *   4. Search filter narrows rows client-side without re-querying.
 *   5. Records render with their event, level, category, and
 *      context decoded.
 *   6. last_error_at surfaces the "Recent error" pill.
 *   7. Empty results show the empty-state message.
 *   8. Export button triggers a download to /system/logs/export.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
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

// Mock useHelpKey to a no-op so the page doesn't try to register
// help content (LogsPage doesn't actually use it; defensive).
vi.mock("@/hooks/useHelpKey", () => ({
  useHelpKey: () => null,
}));

import { LogsPage } from "@/features/system/LogsPage";

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

function mockResponse(overrides: Partial<{
  records: unknown[];
  total_buffered: number;
  count: number;
  last_error_at: string | null;
}>) {
  return {
    records: [],
    count: 0,
    total_buffered: 0,
    next_cursor: null,
    last_error_at: null,
    buffer_capacity: 5000,
    ...overrides,
  };
}

beforeEach(() => {
  apiGet.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("LogsPage", () => {
  it("fetches /system/logs on mount with default filters", async () => {
    apiGet.mockResolvedValueOnce(mockResponse({}));
    render(withProviders(<LogsPage />));
    await waitFor(() => {
      expect(apiGet).toHaveBeenCalled();
    });
    // Default filters (service=all, no level) produce limit=500 only.
    const url = apiGet.mock.calls[0]?.[0] as string;
    expect(url).toContain("/system/logs");
    expect(url).toContain("limit=500");
    expect(url).not.toContain("service=");
    expect(url).not.toContain("level=");
  });

  it("renders records with event / level / category / context", async () => {
    apiGet.mockResolvedValueOnce(
      mockResponse({
        records: [
          {
            timestamp: "2026-05-18T10:00:00+00:00",
            level: "info",
            logger: "auditarr.api",
            category: "api",
            event: "request.complete",
            context: { request_id: "r-1", duration_ms: 12 },
          },
        ],
        count: 1,
        total_buffered: 1,
      }),
    );
    render(withProviders(<LogsPage />));
    await waitFor(() => {
      expect(screen.getByText("request.complete")).toBeInTheDocument();
    });
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText("info")).toBeInTheDocument();
    expect(screen.getByText(/request_id=r-1/)).toBeInTheDocument();
  });

  it("changing the service filter re-fetches with service= query param", async () => {
    apiGet
      .mockResolvedValueOnce(mockResponse({}))
      .mockResolvedValueOnce(mockResponse({}));
    render(withProviders(<LogsPage />));
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("service filter"), {
      target: { value: "worker" },
    });
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
    const url = apiGet.mock.calls.at(-1)?.[0] as string;
    expect(url).toContain("service=worker");
  });

  it("changing the level filter adds level= query param", async () => {
    apiGet
      .mockResolvedValueOnce(mockResponse({}))
      .mockResolvedValueOnce(mockResponse({}));
    render(withProviders(<LogsPage />));
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("level filter"), {
      target: { value: "error" },
    });
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
    const url = apiGet.mock.calls.at(-1)?.[0] as string;
    expect(url).toContain("level=error");
  });

  it("search filter narrows rows client-side", async () => {
    apiGet.mockResolvedValue(
      mockResponse({
        records: [
          {
            timestamp: "2026-05-18T10:00:00+00:00",
            level: "info",
            logger: "auditarr.api",
            category: "api",
            event: "request.complete",
            context: {},
          },
          {
            timestamp: "2026-05-18T10:00:01+00:00",
            level: "warning",
            logger: "auditarr.worker",
            category: "worker",
            event: "rule.applied",
            context: {},
          },
        ],
        count: 2,
        total_buffered: 2,
      }),
    );
    render(withProviders(<LogsPage />));
    await waitFor(() => {
      expect(screen.getByText("request.complete")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText("search filter"), {
      target: { value: "rule" },
    });
    // Only the rule.applied row should remain.
    expect(screen.queryByText("request.complete")).toBeNull();
    expect(screen.getByText("rule.applied")).toBeInTheDocument();
  });

  it("renders the recent-error pill when last_error_at is set", async () => {
    apiGet.mockResolvedValueOnce(
      mockResponse({
        last_error_at: "2026-05-18T10:00:00+00:00",
      }),
    );
    render(withProviders(<LogsPage />));
    await waitFor(() => {
      expect(
        screen.getByTestId("recent-error-indicator"),
      ).toBeInTheDocument();
    });
  });

  it("shows the empty-state message when no records match", async () => {
    apiGet.mockResolvedValueOnce(mockResponse({}));
    render(withProviders(<LogsPage />));
    // v1.9.1: distinguished "buffer empty" from "filter
    // mismatch." With total_buffered=0 (the default mockResponse
    // shape) the page now explains the buffer is still filling,
    // not that the filter dropped everything.
    await waitFor(() => {
      expect(
        screen.getByText(/no records in the buffer yet/i),
      ).toBeInTheDocument();
    });
  });

  it("offers a clear-filter shortcut when a filter hides all rows", async () => {
    // v1.9.1: filter-active + records-in-buffer + zero-matches
    // surfaces a "Clear filters" CTA so the operator doesn't
    // have to manually walk every dropdown back to default.
    apiGet.mockResolvedValueOnce(
      mockResponse({
        records: [
          {
            timestamp: "2026-05-18T10:00:00+00:00",
            level: "info",
            logger: "auditarr.api",
            category: "api",
            event: "api.request",
            context: { path: "/api/v1/health" },
          },
        ],
        count: 1,
        total_buffered: 1,
      }),
    );
    render(withProviders(<LogsPage />));
    await waitFor(() =>
      expect(screen.getByText("api.request")).toBeInTheDocument(),
    );
    // Narrow with a search that won't match anything in the
    // single row above.
    fireEvent.change(screen.getByLabelText("search filter"), {
      target: { value: "definitely-not-there" },
    });
    await waitFor(() => {
      expect(screen.getByText(/clear filters/i)).toBeInTheDocument();
    });
  });

  it("export button fetches the NDJSON endpoint with auth", async () => {
    apiGet.mockResolvedValueOnce(mockResponse({}));
    render(withProviders(<LogsPage />));
    await waitFor(() => expect(apiGet).toHaveBeenCalled());

    // Stub global fetch + URL.createObjectURL for the blob path.
    const blob = new Blob(['{"event":"test"}\n'], {
      type: "application/x-ndjson",
    });
    const fetchSpy = vi.fn(async () => ({
      ok: true,
      status: 200,
      blob: async () => blob,
      headers: new Headers({
        "content-disposition": 'attachment; filename="audit.ndjson"',
      }),
    }));
    (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchSpy as never;
    const createUrl = vi.fn(() => "blob:mock-url");
    (globalThis as unknown as { URL: typeof URL }).URL.createObjectURL = createUrl;
    (globalThis as unknown as { URL: typeof URL }).URL.revokeObjectURL = vi.fn();
    // Seed the localStorage so the bearer header gets added.
    localStorage.setItem(
      "auditarr.auth",
      JSON.stringify({
        state: { tokens: { accessToken: "tok-abc" } },
      }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: /export ndjson/i }),
    );
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const calls = fetchSpy.mock.calls as unknown as [string, RequestInit][];
    const url = calls[0]![0];
    expect(url).toContain("/api/v1/system/logs/export");
    const init = calls[0]![1];
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok-abc",
    );
  });
});
