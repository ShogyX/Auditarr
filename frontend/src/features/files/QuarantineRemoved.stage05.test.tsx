/**
 * Stage 05 (v1.7) — quarantine UI is gone.
 *
 * Plan addendum §A.0: "UI/API surfaces lose the quarantine toggle."
 *
 * This file is the frontend-side regression guard for the
 * quarantine retirement. It mounts the Files page (which renders
 * the toolbar, table, and selection bar at once) plus the file
 * detail drawer and asserts that none of the pre-Stage-05
 * quarantine affordances are present:
 *
 *   - No "Quarantined" Pill in the Files table filename cell.
 *   - No quarantine view <select> in the Files toolbar.
 *   - No "Quarantine" / "Restore" buttons in the file detail
 *     drawer.
 *   - No "Quarantine" button in the selection bar when a row is
 *     selected.
 *   - The Files-list request URL carries no ``quarantined`` or
 *     ``include_quarantined`` query params.
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
import { FileDetailDrawer } from "@/features/files/FileDetailDrawer";
import { useFilesPrefs } from "@/stores/filesPrefsStore";

const FILE_ONE = {
  id: "m-r1",
  library_id: "lib-1",
  path: "/data/Movies/a.mkv",
  relative_path: "Movies/a.mkv",
  filename: "a.mkv",
  extension: "mkv",
  size_bytes: 1_000_000,
  mtime: "2026-05-01T00:00:00Z",
  category: "media",
  severity: "warn",
  severity_rank: 30,
  container: "matroska",
  video_codec: "hevc",
  audio_codec: "aac",
  width: 1920,
  height: 1080,
  has_subtitles: false,
  is_orphaned: false,
  tags: [],
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

function mediaCallPaths(): string[] {
  return apiGet.mock.calls
    .map(([p]) => p as string)
    .filter((p) => typeof p === "string" && p.startsWith("/media"));
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  if (typeof window !== "undefined") {
    window.history.replaceState({}, "", "/files");
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
    sort: { key: "severity", dir: "desc" },
  });
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/libraries") return [{ id: "lib-1", name: "Movies" }];
    if (path.startsWith("/dashboard/categories")) return [];
    if (path.startsWith("/optimization/profiles")) return [];
    if (path.startsWith("/media") && !path.includes("/m-")) {
      return {
        items: [FILE_ONE],
        total: 1,
        offset: 0,
        limit: 50,
      };
    }
    // Per-file detail fetch in the drawer test.
    if (path.startsWith("/media/m-r1") && !path.includes("evaluations") && !path.includes("/tags")) {
      return {
        ...FILE_ONE,
        duration_seconds: 5400,
        bitrate_kbps: 4500,
        subtitle_codec: null,
        framerate: 23.976,
        subtitle_languages: null,
        audio_languages: null,
        probe: null,
        probe_failed: false,
        probe_error: null,
        last_scan_id: null,
        seen_at: "2026-05-01T00:00:00Z",
      };
    }
    if (path.includes("/evaluations")) return [];
    if (path.endsWith("/tags")) return [];
    if (path.startsWith("/playback")) return { items: [] };
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 05 — quarantine UI removed", () => {
  it("Files table has no 'Quarantined' badge in the filename cell", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");
    // No pill labelled "Quarantined" anywhere.
    expect(screen.queryByLabelText("Quarantined")).toBeNull();
    expect(screen.queryByText("Quarantined")).toBeNull();
    // Also no "quarantined" lowercase pill that the drawer used
    // to render.
    expect(screen.queryByText("quarantined")).toBeNull();
  });

  it("Files toolbar has no quarantine view <select>", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");
    // Stage 27's select had aria-label="Quarantine view mode";
    // Stage 05 removed it.
    expect(
      screen.queryByLabelText(/quarantine view mode/i),
    ).toBeNull();
    // None of the option labels appear either.
    expect(screen.queryByText(/hide quarantined/i)).toBeNull();
    expect(screen.queryByText(/include quarantined/i)).toBeNull();
    expect(screen.queryByText(/quarantined only/i)).toBeNull();
  });

  it("Files-list request URL has no ``quarantined`` or ``include_quarantined`` params", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );
    const paths = mediaCallPaths();
    for (const path of paths) {
      // Use word boundaries — ``include_tags`` mustn't trip this
      // by accident.
      expect(path).not.toMatch(/[?&]quarantined=/);
      expect(path).not.toMatch(/[?&]include_quarantined=/);
    }
  });

  it("selection bar has no Quarantine button when rows are selected", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");

    // Click the row's selection checkbox so the selection bar
    // appears. The row uses a click handler on the row container
    // with a checkbox role.
    const rowCheckbox = screen
      .getAllByRole("checkbox")
      .find((el) => el.getAttribute("aria-label")?.toLowerCase().includes("select a.mkv"));
    if (rowCheckbox) {
      fireEvent.click(rowCheckbox);
    } else {
      // Fall back: click the row directly. Tests for other Stage
      // pages take this path too.
      fireEvent.click(screen.getByText("a.mkv"));
    }

    // Whether or not the selection bar mounted, there must be no
    // button labelled "Quarantine" on the page.
    expect(
      screen.queryByRole("button", { name: /^quarantine$/i }),
    ).toBeNull();
  });

  it("FileDetailDrawer has no Quarantine / Restore buttons", async () => {
    // Drawer mounts the per-file detail fetch — the apiGet mock
    // above answers /media/m-r1.
    render(
      wrap(
        <FileDetailDrawer
          file={FILE_ONE}
          onClose={() => {}}
        />,
      ),
    );

    // Wait for the detail fetch to resolve so the drawer is in
    // its fully-rendered state.
    await waitFor(() => {
      expect(apiGet).toHaveBeenCalled();
    });

    // No "Quarantine" button.
    expect(
      screen.queryByRole("button", { name: /^quarantine$/i }),
    ).toBeNull();
    // No "Restore" button (Stage 27 used it for un-quarantine).
    expect(
      screen.queryByRole("button", { name: /^restore$/i }),
    ).toBeNull();
    // No "Quarantine reason: ..." line.
    expect(
      screen.queryByText(/quarantine reason:/i),
    ).toBeNull();
  });
});
