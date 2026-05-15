/**
 * Stage 27 — Files page reprobe + quarantine tests.
 *
 * Pins the operational contracts of the new Files surface
 * additions:
 *
 *   - Re-probe button in the selection bar POSTs to
 *     /media/bulk/reprobe with the selected ids.
 *   - Quarantine button prompts for an optional reason and POSTs
 *     to /media/bulk/quarantine.
 *   - Quarantine view-mode dropdown drives the right server
 *     params (hide → none, include → include_quarantined=true,
 *     only → quarantined=true).
 *   - Table rows with quarantined=true render a "Quarantined" pill.
 *
 * The drawer-level Reprobe / Quarantine / Restore buttons live in
 * a separate file (FileDetailDrawer.tsx); pinned in their own
 * suite.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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
import { MemoryRouter } from "react-router-dom";

const apiGet = vi.fn();
const apiPost = vi.fn();

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

vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: {
      accessToken: "x",
      refreshToken: "x",
      expiresAt: Date.now() + 6e4,
    },
    user: { id: "u1", username: "tester", role: "admin" },
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

import { FilesPage } from "@/features/files/FilesPage";

// ── Fixtures ─────────────────────────────────────────────────
const FILE_A = {
  id: "m-aaa",
  library_id: "lib-1",
  path: "/data/Movies/a.mkv",
  relative_path: "Movies/a.mkv",
  filename: "a.mkv",
  extension: "mkv",
  size_bytes: 1_000_000,
  mtime: "2026-05-10T12:00:00Z",
  category: "media",
  severity: "ok",
  severity_rank: 10,
  container: "matroska",
  video_codec: "h264",
  audio_codec: "aac",
  width: 1920,
  height: 1080,
  has_subtitles: false,
  is_orphaned: false,
  quarantined: false,
};

const FILE_QUARANTINED = {
  ...FILE_A,
  id: "m-bbb",
  filename: "b.mkv",
  path: "/data/Movies/b.mkv",
  quarantined: true,
};

function makePage(items: typeof FILE_A[]): {
  items: typeof FILE_A[];
  total: number;
  offset: number;
  limit: number;
} {
  return { items, total: items.length, offset: 0, limit: 50 };
}

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

let lastListUrl = "";

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  lastListUrl = "";

  apiGet.mockImplementation(async (path: string) => {
    if (path === "/libraries") {
      return [{ id: "lib-1", name: "Movies" }];
    }
    if (path.startsWith("/media")) {
      lastListUrl = path;
      // Default fixture: both files present, default filter
      return makePage([FILE_A, FILE_QUARANTINED]);
    }
    return null;
  });
  apiPost.mockImplementation(async (path: string) => {
    if (path === "/media/bulk/reprobe") {
      return {
        files_reprobed: 1,
        files_failed: 0,
        files_orphaned: 0,
        files_not_found: [],
      };
    }
    if (path === "/media/bulk/quarantine") {
      return { files_quarantined: 1, files_not_found: [] };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ────────────────────────────────────────────────────

describe("FilesPage Stage 27 — selection bar wiring", () => {
  it("Re-probe button POSTs to /media/bulk/reprobe", async () => {
    render(wrap(<FilesPage />));
    // Wait for the table to render.
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );

    // Find the row's checkbox; click to select.
    const row = screen.getByText("a.mkv").closest("tr");
    expect(row).toBeTruthy();
    const checkbox = within(row as HTMLElement).getByRole("checkbox");
    fireEvent.click(checkbox);

    // Selection bar should render; click Re-probe.
    const reprobeBtn = await screen.findByRole("button", { name: /re-probe/i });
    fireEvent.click(reprobeBtn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/media/bulk/reprobe",
        expect.objectContaining({ media_ids: ["m-aaa"] }),
      );
    });
  });

  it("Quarantine button prompts for reason and POSTs", async () => {
    // Stub window.prompt to return a reason.
    const originalPrompt = window.prompt;
    window.prompt = vi.fn(() => "broken on disk") as unknown as typeof window.prompt;

    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );

    const row = screen.getByText("a.mkv").closest("tr");
    const checkbox = within(row as HTMLElement).getByRole("checkbox");
    fireEvent.click(checkbox);

    const qBtn = await screen.findByRole("button", { name: /quarantine/i });
    fireEvent.click(qBtn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/media/bulk/quarantine",
        expect.objectContaining({
          media_ids: ["m-aaa"],
          reason: "broken on disk",
        }),
      );
    });

    window.prompt = originalPrompt;
  });

  it("Quarantine prompt cancellation aborts the action", async () => {
    const originalPrompt = window.prompt;
    window.prompt = vi.fn(() => null) as unknown as typeof window.prompt;

    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );

    const row = screen.getByText("a.mkv").closest("tr");
    const checkbox = within(row as HTMLElement).getByRole("checkbox");
    fireEvent.click(checkbox);

    const qBtn = await screen.findByRole("button", { name: /quarantine/i });
    fireEvent.click(qBtn);

    // No POST should have been issued.
    await waitFor(() => {
      const calls = apiPost.mock.calls.map(([p]) => p);
      expect(calls).not.toContain("/media/bulk/quarantine");
    });

    window.prompt = originalPrompt;
  });

  it("Optimize button stays disabled (Stage 28 work)", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );

    const row = screen.getByText("a.mkv").closest("tr");
    const checkbox = within(row as HTMLElement).getByRole("checkbox");
    fireEvent.click(checkbox);

    const optimize = await screen.findByRole("button", { name: /optimize/i });
    expect(optimize).toBeDisabled();
  });
});

describe("FilesPage Stage 27 — quarantine view-mode", () => {
  it("default view excludes quarantined (no quarantine params sent)", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );
    // The default GET should NOT include quarantined= or
    // include_quarantined= params.
    expect(lastListUrl).not.toMatch(/quarantined/);
  });

  it("'Quarantined only' view sends quarantined=true", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );

    const select = screen.getByLabelText(/quarantine view mode/i);
    fireEvent.change(select, { target: { value: "only" } });

    await waitFor(() => {
      expect(lastListUrl).toMatch(/quarantined=true/);
    });
    expect(lastListUrl).not.toMatch(/include_quarantined/);
  });

  it("'Include quarantined' view sends include_quarantined=true", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );

    const select = screen.getByLabelText(/quarantine view mode/i);
    fireEvent.change(select, { target: { value: "include" } });

    await waitFor(() => {
      expect(lastListUrl).toMatch(/include_quarantined=true/);
    });
    // Should NOT also send quarantined=...
    expect(lastListUrl).not.toMatch(/[?&]quarantined=/);
  });
});

describe("FilesPage Stage 27 — table rendering", () => {
  it("renders a Quarantined pill on quarantined rows", async () => {
    // Use include view so both files appear.
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(screen.queryByText("a.mkv")).toBeInTheDocument(),
    );
    const select = screen.getByLabelText(/quarantine view mode/i);
    fireEvent.change(select, { target: { value: "include" } });

    // After refetch, b.mkv (quarantined) should render with the pill.
    await waitFor(() => {
      const row = screen.queryByText("b.mkv")?.closest("tr") as HTMLElement | null;
      expect(row).toBeTruthy();
      expect(within(row!).getByText(/quarantined/i)).toBeInTheDocument();
    });

    // The clean file a.mkv should NOT carry the pill.
    const aRow = screen.getByText("a.mkv").closest("tr") as HTMLElement;
    expect(within(aRow).queryByText(/quarantined/i)).not.toBeInTheDocument();
  });
});
