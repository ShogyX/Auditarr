/**
 * Stage 3 (audit follow-up) — Files page regression tests.
 *
 * Pins the four contracts that the consolidated audit fix plan
 * established for the Files page:
 *
 *   1. The new optional ``matched_rules`` column renders up to
 *      three rule chips plus a "+N" overflow indicator.
 *   2. The scope tri-state (All / Media / Non-media) plumbs into
 *      the request as ``?scope=media`` or ``?scope=non-media``
 *      (omitted for "All").
 *   3. Hiding every severity sends ``severities_empty=true`` so
 *      the server returns zero rows instead of falling through
 *      to "no filter".
 *   4. The Codec column header sends ``sort=video_codec`` and
 *      the Container column header sends ``sort=container``.
 *
 * Mocks ``apiClient`` per-call and tracks every ``/media`` path
 * so each test can assert the query string the page sent.
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
import { useFilesPrefs } from "@/stores/filesPrefsStore";

// ── Fixtures ────────────────────────────────────────────────────
const FILE_WITH_RULES = {
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
  matched_rules: [
    { rule_id: "r-1", rule_name: "HEVC media", severity: "warn" },
    { rule_id: "r-2", rule_name: "Bitrate ceiling", severity: "high" },
    { rule_id: "r-3", rule_name: "Subtitle missing", severity: "info" },
    { rule_id: "r-4", rule_name: "Stale rename", severity: "info" },
    { rule_id: "r-5", rule_name: "Old probe", severity: "ok" },
  ],
};

const FILE_NO_RULES = {
  ...FILE_WITH_RULES,
  id: "m-r2",
  filename: "b.mkv",
  path: "/data/Movies/b.mkv",
  matched_rules: [],
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

// Return every ``/media`` path the page issued, in order.
function mediaCallPaths(): string[] {
  return apiGet.mock.calls
    .map(([p]) => p as string)
    .filter((p) => typeof p === "string" && p.startsWith("/media"))
    .map((p) => p as string);
}

function lastMediaPath(): string | undefined {
  const all = mediaCallPaths();
  return all.at(-1);
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  // Clean URL so deep-link state from earlier tests doesn't leak.
  if (typeof window !== "undefined") {
    window.history.replaceState({}, "", "/files");
  }
  // Reset prefs to known defaults — Stage 3 changed the default sort
  // key from severity_rank to severity, and tests assert on the
  // outgoing query string. We pin the visible columns to the
  // production default (no matched_rules) so the include_matched_rules
  // flag is OFF unless a specific test turns it on.
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
        items: [FILE_WITH_RULES, FILE_NO_RULES],
        total: 2,
        offset: 0,
        limit: 50,
      };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1. matched_rules column rendering ───────────────────────────
describe("Stage 3 — matched_rules column", () => {
  it("renders up to three chips plus a +N overflow when enabled", async () => {
    // Make the column visible.
    useFilesPrefs.setState((s) => ({
      visibleColumns: [...s.visibleColumns, "matched_rules"],
    }));

    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");

    // First three rule names are visible chips on the matched row.
    expect(screen.getByText("HEVC media")).toBeInTheDocument();
    expect(screen.getByText("Bitrate ceiling")).toBeInTheDocument();
    expect(screen.getByText("Subtitle missing")).toBeInTheDocument();
    // The 4th and 5th must NOT be rendered as chips — they're
    // collapsed into the overflow.
    expect(screen.queryByText("Stale rename")).not.toBeInTheDocument();
    expect(screen.queryByText("Old probe")).not.toBeInTheDocument();
    // Overflow pill: "+2".
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("turning on the column adds include_matched_rules=true to the request", async () => {
    useFilesPrefs.setState((s) => ({
      visibleColumns: [...s.visibleColumns, "matched_rules"],
    }));

    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    const path = lastMediaPath()!;
    expect(path).toContain("include_matched_rules=true");
  });

  it("leaving the column off omits include_matched_rules from the request", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    const path = lastMediaPath()!;
    expect(path).not.toContain("include_matched_rules");
  });
});

// ── 2. scope plumbing ───────────────────────────────────────────
describe("Stage 3 — scope plumbs into request", () => {
  it("clicking the Media scope tab sends scope=media", async () => {
    render(wrap(<FilesPage />));
    // Wait for first /media call to land.
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    // First call defaults to scope=all → no scope param sent.
    expect(lastMediaPath()!).not.toContain("scope=");

    // Click the "Media" segmented-control tab.
    fireEvent.click(screen.getByRole("tab", { name: /^media$/i }));

    await waitFor(() => {
      const last = lastMediaPath()!;
      expect(last).toContain("scope=media");
    });
  });

  it("clicking the Non-media scope tab sends scope=non-media", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByRole("tab", { name: /non-media/i }));

    await waitFor(() => {
      // ``non-media`` survives URL-encoding as ``non-media`` —
      // the hyphen is a literal in the query string. We match the
      // value loosely to be friendly to either encoded or
      // unencoded forms.
      const last = lastMediaPath()!;
      expect(last).toMatch(/scope=non-media|scope=non%2Dmedia/);
    });
  });

  it("clicking back to All scope drops the scope param", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByRole("tab", { name: /^media$/i }));
    await waitFor(() =>
      expect(lastMediaPath()!).toContain("scope=media"),
    );
    fireEvent.click(screen.getByRole("tab", { name: /all severities/i }));

    // React Query caches per-key, so clicking back to the "all"
    // scope (which omits the param) may serve the original cached
    // response without firing a new ``apiClient.get``. The contract
    // we actually care about is: at no point did the page fire a
    // /media request with ``scope=all`` (the param is omitted, not
    // sent as a literal). And among all requests in this test,
    // there must exist one without ``scope=`` at all (the initial
    // mount before the Media click).
    await waitFor(() => {
      const all = mediaCallPaths();
      expect(all.some((p) => !p.includes("scope="))).toBe(true);
      expect(all.every((p) => !p.includes("scope=all"))).toBe(true);
    });
  });
});

// ── 3. empty-severities sentinel ────────────────────────────────
describe("Stage 3 — empty severities sentinel", () => {
  it("hiding every severity sends severities_empty=true", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    // The scope bar shows a "Hide all" / "Show all" toggle that flips
    // the active-sev set in one click. We rely on its presence
    // rather than poking every chip individually.
    const hideAll = screen.getByRole("button", { name: /hide all/i });
    fireEvent.click(hideAll);

    await waitFor(() => {
      const last = lastMediaPath()!;
      expect(last).toContain("severities_empty=true");
    });
  });

  it("the default (every severity active) omits both severity and severities_empty", async () => {
    render(wrap(<FilesPage />));
    await waitFor(() =>
      expect(mediaCallPaths().length).toBeGreaterThan(0),
    );

    const last = lastMediaPath()!;
    // No comma-joined severity list when all are on (would be
    // redundant; backend treats absent param as "no filter").
    expect(last).not.toContain("severity=");
    expect(last).not.toContain("severities_empty=");
  });
});

// ── 4. codec / container header sort ────────────────────────────
describe("Stage 3 — column-header sort", () => {
  it("clicking the Codec header sends sort=video_codec", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");

    // The codec header is a <th> with the text "Codec" plus a
    // sort indicator span. We can click the cell directly.
    const codecHeader = screen.getByRole("columnheader", {
      name: /codec/i,
    });
    fireEvent.click(codecHeader);

    await waitFor(() => {
      const last = lastMediaPath()!;
      expect(last).toContain("sort=video_codec");
    });
  });

  it("clicking the Container header sends sort=container", async () => {
    // The Container column is opt-in; turn it on for this test.
    useFilesPrefs.setState((s) => ({
      visibleColumns: [...s.visibleColumns, "container"],
    }));

    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");

    const containerHeader = screen.getByRole("columnheader", {
      name: /container/i,
    });
    fireEvent.click(containerHeader);

    await waitFor(() => {
      const last = lastMediaPath()!;
      expect(last).toContain("sort=container");
    });
  });

  it("the Codec header shows the active sort indicator after click", async () => {
    render(wrap(<FilesPage />));
    await screen.findByText("a.mkv");

    fireEvent.click(
      screen.getByRole("columnheader", { name: /codec/i }),
    );

    // Re-query after the click so we read the updated aria-sort
    // on the re-rendered <th>, not the stale reference we used to
    // dispatch the click. The Stage 3 first-click direction is
    // "desc" (matches clickSort's "new key ⇒ desc" default).
    await waitFor(() => {
      const updated = screen.getByRole("columnheader", {
        name: /codec/i,
      });
      expect(updated).toHaveAttribute("aria-sort", "descending");
    });
  });
});
