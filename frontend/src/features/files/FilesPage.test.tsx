/**
 * Stage 23 — Files page behavior tests.
 *
 * Pins the operational contracts of the rewritten Files page:
 *
 *   - sort column-click toggles asc/desc and rewrites the API query
 *   - selection bar appears with the right count after row checks
 *   - "select all on this page" toggles the visible rows together
 *   - bulk re-evaluate posts the selected ids
 *   - clicking a row opens the detail drawer
 *
 * The drawer's content (probe panel, language tracks, evaluations
 * list) is exercised separately so this file stays focused on the
 * page-level behavior.
 *
 * Mocks ``apiClient`` per-call so we can both serve realistic media
 * page data AND observe the GET / POST traffic the page issues.
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

// Stub the scan-progress websocket hook so it doesn't try to open a
// real WS during the test. The page only consumes ``runId``,
// ``recentlyCompleted``, ``filesSeen`` from it.
vi.mock("@/hooks/useScanProgress", () => ({
  useScanProgress: () => ({
    runId: null,
    recentlyCompleted: false,
    filesSeen: 0,
  }),
}));

import { FilesPage } from "@/features/files/FilesPage";
import { useFilesPrefs } from "@/stores/filesPrefsStore";

// ── Fixtures ──────────────────────────────────────────────────
const LIBRARIES = [
  {
    id: "lib-1",
    name: "Movies",
    root_path: "/data/movies",
    kind: "movies",
    enabled: true,
    scan_interval_minutes: 0,
    integration_link: null,
    last_scan_at: null,
    last_scan_status: null,
    last_scan_file_count: null,
    created_at: "2026-05-10T00:00:00Z",
    updated_at: "2026-05-10T00:00:00Z",
  },
];

const FILE_A = {
  id: "f-aaa",
  library_id: "lib-1",
  path: "/data/movies/A.mkv",
  relative_path: "A.mkv",
  filename: "A.mkv",
  extension: "mkv",
  size_bytes: 1_000_000,
  mtime: "2026-05-01T00:00:00Z",
  category: "media",
  severity: "warn",
  severity_rank: 30,
  container: "matroska",
  video_codec: "h264",
  audio_codec: "ac3",
  width: 1920,
  height: 1080,
  has_subtitles: true,
  is_orphaned: false,
};

const FILE_B = {
  ...FILE_A,
  id: "f-bbb",
  path: "/data/movies/B.mkv",
  filename: "B.mkv",
  size_bytes: 2_000_000,
  severity: "high",
  severity_rank: 40,
};

const MEDIA_PAGE = {
  items: [FILE_A, FILE_B],
  total: 2,
  offset: 0,
  limit: 50,
};

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

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  // Clear both the persisted localStorage payload AND the in-memory
  // zustand state. The persist middleware reads localStorage on
  // module load only, so a previous test's setSort/toggleColumn
  // mutation would otherwise leak forward.
  if (typeof localStorage !== "undefined") {
    localStorage.removeItem("auditarr.files.prefs");
  }
  useFilesPrefs.setState({
    visibleColumns: [
      "filename",
      "category",
      "severity",
      "size",
      "codec",
      "resolution",
      "subs",
    ],
    pageSize: 50,
    sort: { key: "severity_rank", dir: "desc" },
  });
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/libraries") return LIBRARIES;
    if (path.startsWith("/media?") || path === "/media") return MEDIA_PAGE;
    if (path.startsWith("/media/") && path.endsWith("/evaluations")) return [];
    if (path.startsWith("/media/")) return { ...FILE_A, probe: null };
    if (path.startsWith("/scans")) return [];
    if (path === "/system/scan-progress" || path === "/scan/progress") {
      return null;
    }
    return null;
  });
  apiPost.mockResolvedValue({ files_evaluated: 0, files_not_found: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────

describe("FilesPage", () => {
  it("renders rows once the media list resolves", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");
    expect(screen.getByText("B.mkv")).toBeInTheDocument();
  });

  it("clicking a sortable header rewrites the API query with sort + sort_dir", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    // The "Size" column is sortable on size_bytes. After each click
    // the table refetches and briefly shows its loading state, so we
    // re-find the header after waiting for the row to reappear.
    fireEvent.click(screen.getByRole("columnheader", { name: /size/i }));

    await waitFor(() => {
      const sortCall = apiGet.mock.calls.find(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("sort=size_bytes") &&
          call[0].includes("sort_dir=desc"),
      );
      expect(
        sortCall,
        "expected a /media call with sort=size_bytes&sort_dir=desc",
      ).toBeDefined();
    });

    // Wait for the table to re-render (the row reappears once the
    // refetch resolves).
    await screen.findByText("A.mkv");

    // Click again: should flip to asc.
    fireEvent.click(screen.getByRole("columnheader", { name: /size/i }));
    await waitFor(() => {
      const ascCall = apiGet.mock.calls.find(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("sort=size_bytes") &&
          call[0].includes("sort_dir=asc"),
      );
      expect(
        ascCall,
        "expected the second click to request asc",
      ).toBeDefined();
    });
  });

  it("checking a row reveals the selection bar with the right count", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    // Two row-level checkboxes (the header has one for select-all). The
    // first match is the select-all box; subsequent matches are rows.
    const allCheckboxes = screen.getAllByRole("checkbox");
    const rowCheckbox = allCheckboxes.find((c) =>
      c.getAttribute("aria-label")?.startsWith("Select A.mkv"),
    );
    expect(rowCheckbox).toBeDefined();
    fireEvent.click(rowCheckbox!);

    expect(await screen.findByText("1 selected")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /re-evaluate rules/i }),
    ).toBeInTheDocument();
  });

  it("select-all header checkbox toggles every visible row", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    const selectAll = screen.getByRole("checkbox", {
      name: /select all on this page/i,
    });
    fireEvent.click(selectAll);

    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    // The aria-label flips after selection.
    const deselect = screen.getByRole("checkbox", {
      name: /deselect all on this page/i,
    });
    fireEvent.click(deselect);
    await waitFor(() =>
      expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument(),
    );
  });

  it("Re-evaluate rules POSTs the selected ids", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    const selectAll = screen.getByRole("checkbox", {
      name: /select all on this page/i,
    });
    fireEvent.click(selectAll);
    await screen.findByText("2 selected");

    fireEvent.click(
      screen.getByRole("button", { name: /re-evaluate rules/i }),
    );

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/media/bulk/reevaluate", {
        media_ids: expect.arrayContaining(["f-aaa", "f-bbb"]),
      });
    });
  });

  it("clicking a row opens the detail drawer with the file's filename", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    // Click the row — testing-library's user event model treats the
    // row click as bubbling from the cell content.
    const cell = screen.getAllByText("A.mkv")[0]!;
    fireEvent.click(cell);

    const drawer = await screen.findByRole("dialog", {
      name: /details for a\.mkv/i,
    });
    expect(within(drawer).getByText("A.mkv")).toBeInTheDocument();
  });

  it("column visibility menu toggles columns", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    // The "Updated" column is NOT visible by default.
    expect(
      screen.queryByRole("columnheader", { name: /updated/i }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /columns/i }));
    const menu = await screen.findByRole("menu");
    const updatedCheckbox = within(menu).getByLabelText(/updated/i);
    fireEvent.click(updatedCheckbox);

    // Header should now appear.
    await waitFor(() =>
      expect(
        screen.getByRole("columnheader", { name: /updated/i }),
      ).toBeInTheDocument(),
    );
  });

  it("changing a filter clears the current selection", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("A.mkv");

    fireEvent.click(
      screen.getByRole("checkbox", { name: /select all on this page/i }),
    );
    await screen.findByText("2 selected");

    // Type into the search box.
    const search = screen.getByPlaceholderText(/search path or filename/i);
    fireEvent.change(search, { target: { value: "test" } });

    await waitFor(() =>
      expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument(),
    );
  });
});
