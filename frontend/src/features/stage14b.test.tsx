/**
 * Stage 14b (audit follow-up) — Matched files tab + deep-link.
 *
 * Pins:
 *   1. MatchedFilesTab renders the per-rule list, including a link
 *      to ``/files?file_id=...`` for each row.
 *   2. MatchedFilesTab shows the empty state when the rule has no
 *      evaluations.
 *   3. RuleEditorTabStrip exposes "Matched files" between Dry-run
 *      and JSON.
 *   4. The Files page's ``useFilesPageState`` honors a
 *      ``?file_id=mf-xxx`` URL param by fetching the file and
 *      opening the drawer.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
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

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: vi.fn(async () => null),
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

import { MatchedFilesTab } from "@/features/rules/MatchedFilesTab";
import { RuleEditorTabStrip } from "@/features/rules/RuleEditorTabStrip";
import { useFilesPageState } from "@/features/files/useFilesPageState";

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
  if (typeof window !== "undefined") {
    window.history.replaceState({}, "", "/files");
  }
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1+2. MatchedFilesTab rendering ──────────────────────────────
describe("Stage 14b — MatchedFilesTab", () => {
  it("renders matched-file rows with a deep-link to Files", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/rules/r-1/matched-files")) {
        return [
          {
            media_file_id: "mf-a",
            library_id: "lib-1",
            path: "/data/Movies/a.mkv",
            filename: "a.mkv",
            severity: "error",
            severity_rank: 50,
            evaluated_at: "2026-05-14T10:00:00Z",
          },
          {
            media_file_id: "mf-b",
            library_id: "lib-1",
            path: "/data/Movies/b.mkv",
            filename: "b.mkv",
            severity: "warn",
            severity_rank: 30,
            evaluated_at: "2026-05-14T09:00:00Z",
          },
        ];
      }
      return null;
    });

    render(wrap(<MatchedFilesTab ruleId="r-1" />));

    await waitFor(() => {
      expect(
        screen.getByTestId("rule-matched-files-table"),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("a.mkv")).toBeInTheDocument();
    expect(screen.getByText("b.mkv")).toBeInTheDocument();
    // Severity pills.
    expect(screen.getByText("error")).toBeInTheDocument();
    expect(screen.getByText("warn")).toBeInTheDocument();
    // Each filename links to /files?file_id=<media_file_id>.
    const aLink = screen.getByText("a.mkv").closest("a");
    expect(aLink).not.toBeNull();
    expect(aLink!.getAttribute("href")).toBe("/files?file_id=mf-a");
  });

  it("shows the empty state when the rule has no matches", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/rules/r-2/matched-files")) {
        return [];
      }
      return null;
    });

    render(wrap(<MatchedFilesTab ruleId="r-2" />));

    await waitFor(() => {
      expect(screen.getByText("No matches")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("rule-matched-files-table"),
    ).toBeNull();
  });
});

// ── 3. RuleEditorTabStrip exposes Matched files ─────────────────
describe("Stage 14b — RuleEditorTabStrip", () => {
  it("renders the Matched files tab between Dry-run and JSON", () => {
    render(
      wrap(
        <RuleEditorTabStrip tab="visual" onTab={() => {}} jsonError={null} />,
      ),
    );
    const visual = screen.getByText("Visual");
    const dryrun = screen.getByText("Dry-run");
    const matched = screen.getByText("Matched files");
    const json = screen.getByText("JSON");
    // Order: Visual, Dry-run, Matched files, JSON. Compare via
    // DOMNode order using compareDocumentPosition.
    expect(
      visual.compareDocumentPosition(dryrun) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      dryrun.compareDocumentPosition(matched) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      matched.compareDocumentPosition(json) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

// ── 4. Files-page ?file_id= deep-link ───────────────────────────
describe("Stage 14b — Files page ?file_id= deep-link", () => {
  function HookWrapper({ children }: { children: ReactNode }) {
    return <>{wrap(children)}</>;
  }

  it("opens the drawer for the named file on mount", async () => {
    // Set the URL BEFORE the hook mounts (the effect reads
    // window.location.search synchronously).
    window.history.replaceState({}, "", "/files?file_id=mf-deep-1");

    apiGet.mockImplementation(async (path: string) => {
      if (path === "/media/mf-deep-1") {
        return {
          id: "mf-deep-1",
          library_id: "lib-1",
          path: "/data/Movies/x.mkv",
          relative_path: "Movies/x.mkv",
          filename: "x.mkv",
          extension: "mkv",
          size_bytes: 1,
          mtime: "2026-05-01T00:00:00Z",
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
      }
      if (path === "/libraries") return [];
      if (path.startsWith("/optimization/profiles")) return [];
      if (path.startsWith("/media")) {
        return { items: [], total: 0, offset: 0, limit: 50 };
      }
      return null;
    });

    const { result } = renderHook(() => useFilesPageState(), {
      wrapper: HookWrapper,
    });

    // Wait for the effect to resolve the fetch and pop the drawer.
    await waitFor(() => {
      expect(result.current.drawerFile?.id).toBe("mf-deep-1");
    });

    // The ``file_id`` param is stripped from the URL after the
    // fetch completes — back-navigation must NOT re-open the
    // drawer.
    await waitFor(() => {
      expect(window.location.search).not.toContain("file_id=");
    });
  });

  it("ignores ?file_id when the fetch 404s (e.g. evicted file)", async () => {
    window.history.replaceState({}, "", "/files?file_id=missing");

    apiGet.mockImplementation(async (path: string) => {
      if (path === "/media/missing") {
        const err = new Error("Not found");
        // The apiClient mock throws; the deep-link handler
        // swallows the rejection.
        throw err;
      }
      if (path === "/libraries") return [];
      if (path.startsWith("/optimization/profiles")) return [];
      if (path.startsWith("/media")) {
        return { items: [], total: 0, offset: 0, limit: 50 };
      }
      return null;
    });

    const { result } = renderHook(() => useFilesPageState(), {
      wrapper: HookWrapper,
    });

    // Wait long enough for the rejected promise to settle.
    await waitFor(() => {
      expect(window.location.search).not.toContain("file_id=");
    });
    // Drawer stays closed.
    expect(result.current.drawerFile).toBeNull();
  });
});
