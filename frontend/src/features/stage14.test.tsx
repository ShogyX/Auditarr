/**
 * Stage 14 (audit follow-up) — operator tooling frontend tests.
 *
 * Five surfaces pinned in one test file to keep the mock scaffold
 * shared:
 *
 *   1. ScanDetailPage — failed scans surface their error blob in
 *      a <pre> block.
 *   2. HousekeepingActionsCard — Run now POSTs to the endpoint and
 *      surfaces the row count in a toast.
 *   3. SystemMaintenanceCard — Reload docs POSTs to /docs/reload
 *      and toasts the page count.
 *   4. AuditLogPage — initial fetch renders rows; "Load more"
 *      appends without duplicating.
 *   5. OptimizationQueueRow — Run now button hidden on non-queued
 *      rows.
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
import { MemoryRouter, Route, Routes } from "react-router-dom";

const apiGet = vi.fn();
const apiPost = vi.fn();
const toastSpy = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
    patch: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

vi.mock("@/lib/toast", () => ({
  toast: (...args: unknown[]) => toastSpy(...args),
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

import { AuditLogPage } from "@/features/audit/AuditLogPage";
import { HousekeepingActionsCard } from "@/features/settings/HousekeepingActionsCard";
import { OptimizationQueueRow } from "@/features/optimization/OptimizationQueueRow";
import { ScanDetailPage } from "@/features/scans/ScanDetailPage";
import { SystemMaintenanceCard } from "@/features/settings/SystemMaintenanceCard";

function wrap(child: ReactNode, initialPath = "/"): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>{child}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  toastSpy.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1. ScanDetailPage failed error block ────────────────────────
describe("Stage 14 — ScanDetailPage", () => {
  it("renders the error block when status === failed", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/scans/scan-abc") {
        return {
          id: "scan-abc",
          library_id: "lib-1",
          mode: "full",
          status: "failed",
          started_at: "2026-05-14T10:00:00Z",
          finished_at: "2026-05-14T10:01:00Z",
          files_seen: 0,
          files_added: 0,
          files_updated: 0,
          files_orphaned: 0,
          probe_failures: 0,
          error:
            "PermissionError: [Errno 13] Permission denied: '/mnt/library/foo'",
          created_at: "2026-05-14T10:00:00Z",
        };
      }
      return null;
    });

    render(
      wrap(
        <Routes>
          <Route path="/scans/:scanId" element={<ScanDetailPage />} />
        </Routes>,
        "/scans/scan-abc",
      ),
    );

    await waitFor(() => {
      expect(screen.getByTestId("scan-error-block")).toBeInTheDocument();
    });
    const pre = screen.getByTestId("scan-error-block");
    expect(pre.textContent).toContain("PermissionError");
  });

  it("HIDES the error block when status !== failed", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/scans/scan-ok") {
        return {
          id: "scan-ok",
          library_id: "lib-1",
          mode: "full",
          status: "completed",
          started_at: "2026-05-14T10:00:00Z",
          finished_at: "2026-05-14T10:01:00Z",
          files_seen: 100,
          files_added: 1,
          files_updated: 2,
          files_orphaned: 0,
          probe_failures: 0,
          error: null,
          created_at: "2026-05-14T10:00:00Z",
        };
      }
      return null;
    });

    render(
      wrap(
        <Routes>
          <Route path="/scans/:scanId" element={<ScanDetailPage />} />
        </Routes>,
        "/scans/scan-ok",
      ),
    );

    await waitFor(() => {
      expect(screen.getByText("completed")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("scan-error-block")).toBeNull();
  });
});

// ── 2. HousekeepingActionsCard ─────────────────────────────────
describe("Stage 14 — HousekeepingActionsCard", () => {
  it("Run now POSTs to /system/housekeeping/run and toasts the total", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/housekeeping/last-run") return null;
      return null;
    });
    apiPost.mockImplementation(async (path: string) => {
      if (path === "/system/housekeeping/run") {
        return {
          trigger: "manual",
          notification_deliveries: 3,
          update_checks: 1,
          rule_evaluations: 0,
          job_runs: 2,
          total: 6,
        };
      }
      return null;
    });

    render(wrap(<HousekeepingActionsCard />));

    fireEvent.click(screen.getByRole("button", { name: /run now/i }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/system/housekeeping/run",
        {},
      );
    });
    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalled();
    });
    expect(toastSpy.mock.calls[0]![0]).toMatch(/deleted 6 rows/i);
  });

  it("shows the last-run line including the trigger pill", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/system/housekeeping/last-run") {
        return {
          id: "r-1",
          trigger: "scheduled",
          started_at: "2026-05-14T03:00:00Z",
          finished_at: "2026-05-14T03:00:05Z",
          deliveries_deleted: 12,
          update_checks_deleted: 0,
          rule_evaluations_deleted: 0,
          job_runs_deleted: 5,
          error: null,
        };
      }
      return null;
    });

    render(wrap(<HousekeepingActionsCard />));

    await waitFor(() => {
      expect(
        screen.getByTestId("housekeeping-last-run"),
      ).toBeInTheDocument();
    });
    // Trigger pill renders.
    await waitFor(() =>
      expect(screen.getByText("scheduled")).toBeInTheDocument(),
    );
    // Counters surface in the inline line.
    expect(screen.getByText(/deliveries=12/)).toBeInTheDocument();
  });
});

// ── 3. SystemMaintenanceCard ──────────────────────────────────
describe("Stage 14 — SystemMaintenanceCard", () => {
  it("Reload docs POSTs to /docs/reload and toasts the count", async () => {
    apiPost.mockImplementation(async (path: string) => {
      if (path === "/docs/reload") {
        return { count: 42 };
      }
      return null;
    });

    render(wrap(<SystemMaintenanceCard />));

    fireEvent.click(
      screen.getByRole("button", { name: /reload documentation index/i }),
    );

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/docs/reload", {});
    });
    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalled();
    });
    expect(toastSpy.mock.calls[0]![0]).toMatch(/reloaded 42 pages/i);
  });
});

// ── 4. AuditLogPage initial + load more ────────────────────────
describe("Stage 14 — AuditLogPage", () => {
  it("renders the initial page and appends on Load more without duplicating", async () => {
    // Two simulated pages keyed by whether before_id is present.
    const pageA = Array.from({ length: 100 }, (_, i) => ({
      id: 200 - i,
      occurred_at: `2026-05-14T${String(10 - (i % 10)).padStart(2, "0")}:00:00Z`,
      actor_id: "u-1",
      actor_label: "alice",
      action: `act.${i}`,
      target_type: "rule",
      target_id: `r-${i}`,
      ip_address: "127.0.0.1",
      request_id: null,
      metadata: null,
    }));
    const pageB = Array.from({ length: 50 }, (_, i) => ({
      id: 100 - i,
      occurred_at: `2026-05-13T${String(10 - (i % 10)).padStart(2, "0")}:00:00Z`,
      actor_id: "u-1",
      actor_label: "alice",
      action: `older.${i}`,
      target_type: "rule",
      target_id: `r-${i}`,
      ip_address: "127.0.0.1",
      request_id: null,
      metadata: null,
    }));
    apiGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/audit/log")) {
        if (path.includes("before_id=")) return pageB;
        return pageA;
      }
      return null;
    });

    render(wrap(<AuditLogPage />));

    // First page seeds the buffer.
    await waitFor(() => {
      expect(screen.getByTestId("audit-log-table")).toBeInTheDocument();
    });
    // 100 rows visible after initial.
    expect(screen.getByText("act.0")).toBeInTheDocument();
    expect(screen.getByText("act.99")).toBeInTheDocument();

    // Load more.
    fireEvent.click(screen.getByRole("button", { name: /load more/i }));

    await waitFor(() => {
      // First page B row.
      expect(screen.getByText("older.0")).toBeInTheDocument();
    });
    // No duplication: act.0 still present once.
    const matches = screen.getAllByText("act.0");
    expect(matches.length).toBe(1);
  });
});

// ── 5. OptimizationQueueRow run-now visibility ─────────────────
describe("Stage 14 — OptimizationQueueRow Run now visibility", () => {
  const baseItem = {
    id: "it-1",
    media_file_id: "mf-1",
    profile: "h265-medium",
    queued_by_rule_id: null,
    queued_at: "2026-05-14T10:00:00Z",
    started_at: null,
    finished_at: null,
    progress_pct: 0,
    original_size_bytes: null,
    optimized_size_bytes: null,
    backup_path: null,
    item_metadata: {},
    error: null,
    created_at: "2026-05-14T10:00:00Z",
    updated_at: "2026-05-14T10:00:00Z",
  };

  it("Run now button is VISIBLE on queued rows", () => {
    render(
      wrap(
        <OptimizationQueueRow
          item={{ ...baseItem, status: "queued" }}
        />,
      ),
    );
    expect(
      screen.getByRole("button", { name: /run now/i }),
    ).toBeInTheDocument();
  });

  it("Run now button is HIDDEN on running rows", () => {
    render(
      wrap(
        <OptimizationQueueRow
          item={{ ...baseItem, status: "running", progress_pct: 50 }}
        />,
      ),
    );
    expect(
      screen.queryByRole("button", { name: /run now/i }),
    ).toBeNull();
  });

  it("Run now button is HIDDEN on completed rows", () => {
    render(
      wrap(
        <OptimizationQueueRow
          item={{ ...baseItem, status: "completed", progress_pct: 100 }}
        />,
      ),
    );
    expect(
      screen.queryByRole("button", { name: /run now/i }),
    ).toBeNull();
  });
});
