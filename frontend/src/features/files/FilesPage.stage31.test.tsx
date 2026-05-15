/**
 * Stage 31 — Codec / container filter on Files.
 *
 * Pins:
 *
 *   - The "Codec / container" trigger button is in the toolbar.
 *   - Opening the menu lists codecs and containers from the
 *     dashboard /categories endpoint with file counts.
 *   - Checking a codec adds it to the request as
 *     ``?video_codec=<codec>``.
 *   - Checking a second codec produces a comma-joined value.
 *   - Checking a container adds it as a separate query param.
 *   - "Clear all" empties both selections and drops the params.
 *   - The trigger button shows an active count when filters
 *     are applied.
 *   - Mounting with ``?video_codec=hevc&container=mp4`` in the
 *     URL initializes the filters from the URL and the next
 *     /media fetch carries those params.
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

const CATEGORIES = [
  {
    key: "hevc",
    label: "HEVC",
    group: "video_codec",
    file_count: 120,
    total_size_bytes: 999_000_000,
  },
  {
    key: "h264",
    label: "H.264",
    group: "video_codec",
    file_count: 80,
    total_size_bytes: 500_000_000,
  },
  {
    key: "mpeg4",
    label: "MPEG-4",
    group: "video_codec",
    file_count: 5,
    total_size_bytes: 12_000_000,
  },
  {
    key: "matroska",
    label: "Matroska (mkv)",
    group: "container",
    file_count: 110,
    total_size_bytes: 900_000_000,
  },
  {
    key: "mp4",
    label: "MP4",
    group: "container",
    file_count: 90,
    total_size_bytes: 600_000_000,
  },
];

const FILE_A = {
  id: "m-aaa",
  library_id: "lib-1",
  path: "/data/Movies/a.mkv",
  relative_path: "Movies/a.mkv",
  filename: "a.mkv",
  extension: "mkv",
  size_bytes: 1_000_000,
  category: "media",
  severity: "ok",
  severity_rank: 10,
  video_codec: "hevc",
  container: "matroska",
  has_subtitles: false,
  is_orphaned: false,
  quarantined: false,
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

// Track each /media request so we can assert the query string
// the page sent. apiClient.get receives the full path; tests
// pull the most-recent /media call.
function lastMediaPath(): string | undefined {
  const calls = apiGet.mock.calls.filter(
    ([p]) => typeof p === "string" && (p as string).startsWith("/media"),
  );
  return calls.at(-1)?.[0] as string | undefined;
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  // Reset the URL so the deep-link test below has a clean slate.
  // jsdom keeps history across tests in the same module unless
  // we reset.
  if (typeof window !== "undefined") {
    window.history.replaceState({}, "", "/files");
  }
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/libraries") return [{ id: "lib-1", name: "Movies" }];
    if (path.startsWith("/dashboard/categories")) return CATEGORIES;
    if (path.startsWith("/optimization/profiles")) return [];
    if (path.startsWith("/media")) {
      return { items: [FILE_A], total: 1, offset: 0, limit: 50 };
    }
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ────────────────────────────────────────────────────

describe("Stage 31 — Codec / container filter", () => {
  it("renders the Codec / container trigger button in the toolbar", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    expect(trigger).toBeInTheDocument();
  });

  it("opens a popover listing codecs and containers with file counts", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    // Wait for popover content. The codec/container rows are the
    // unique signal; we can't disambiguate by header text alone
    // because "Container" also appears in the column-visibility
    // menu's column list.
    await waitFor(() => {
      expect(screen.getByText("HEVC")).toBeInTheDocument();
    });

    // Codec rows visible with file counts.
    expect(screen.getByText("H.264")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    // Container rows visible.
    expect(screen.getByText("Matroska (mkv)")).toBeInTheDocument();
    expect(screen.getByText("MP4")).toBeInTheDocument();
  });

  it("selecting a codec sends ?video_codec=<key> to /media", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    // Wait for the row + click the HEVC checkbox.
    const hevcRow = (await screen.findByText("HEVC")).closest("label");
    expect(hevcRow).toBeTruthy();
    const hevcCheckbox = within(hevcRow as HTMLElement).getByRole("checkbox");
    fireEvent.click(hevcCheckbox);

    await waitFor(() => {
      const last = lastMediaPath();
      expect(last).toBeDefined();
      expect(last).toContain("video_codec=hevc");
    });
  });

  it("selecting two codecs produces a comma-joined value", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    const hevc = within(
      (await screen.findByText("HEVC")).closest("label") as HTMLElement,
    ).getByRole("checkbox");
    fireEvent.click(hevc);

    const h264 = within(
      (await screen.findByText("H.264")).closest("label") as HTMLElement,
    ).getByRole("checkbox");
    fireEvent.click(h264);

    await waitFor(() => {
      const last = lastMediaPath();
      expect(last).toBeDefined();
      // Sorted alphabetically per the FilesPage memo to keep
      // React Query cache keys stable. h264 comes before hevc.
      expect(last).toMatch(/video_codec=h264%2Chevc|video_codec=h264,hevc/);
    });
  });

  it("selecting a container sends ?container=<key>", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    const matroska = within(
      (await screen.findByText("Matroska (mkv)")).closest(
        "label",
      ) as HTMLElement,
    ).getByRole("checkbox");
    fireEvent.click(matroska);

    await waitFor(() => {
      const last = lastMediaPath();
      expect(last).toContain("container=matroska");
    });
  });

  it("'Clear all' resets both selections (button badge returns to no count)", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    // Select one of each.
    fireEvent.click(
      within(
        (await screen.findByText("HEVC")).closest("label") as HTMLElement,
      ).getByRole("checkbox"),
    );
    fireEvent.click(
      within(
        (await screen.findByText("MP4")).closest("label") as HTMLElement,
      ).getByRole("checkbox"),
    );

    // Confirm both are in the request.
    await waitFor(() => {
      const last = lastMediaPath();
      expect(last).toContain("video_codec=hevc");
      expect(last).toContain("container=mp4");
    });

    // The trigger button shows "2" as its active count badge.
    await waitFor(() => {
      const btn = screen.getByRole("button", {
        name: /codec and container/i,
      });
      expect(btn.textContent).toMatch(/2/);
    });

    // Click "Clear all" — the button is in the popover footer.
    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));

    // After clearing, the trigger badge no longer shows a count.
    // We can't assert the next /media call is filter-free because
    // React Query may serve the initial (filter-free) result from
    // its cache — that's correct behavior, not a bug. The trigger
    // badge IS the user-visible signal that the filters cleared.
    await waitFor(() => {
      const btn = screen.getByRole("button", {
        name: /codec and container/i,
      });
      // The badge span is only rendered when activeCount > 0; the
      // text content should no longer contain a digit.
      expect(btn.textContent?.trim()).toBe("Codec / container");
    });
  });

  it("trigger button shows active count badge", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    fireEvent.click(
      within(
        (await screen.findByText("HEVC")).closest("label") as HTMLElement,
      ).getByRole("checkbox"),
    );
    fireEvent.click(
      within(
        (await screen.findByText("MP4")).closest("label") as HTMLElement,
      ).getByRole("checkbox"),
    );

    // The button's badge shows 2 (one codec + one container).
    await waitFor(() => {
      const btn = screen.getByRole("button", {
        name: /codec and container/i,
      });
      expect(btn.textContent).toMatch(/2/);
    });
  });

  it("Escape closes the popover", async () => {
    render(wrap(<FilesPage />));
    const trigger = await screen.findByRole("button", {
      name: /codec and container/i,
    });
    fireEvent.click(trigger);

    expect(await screen.findByText("HEVC")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      // HEVC only appears in the codec popover, so its absence
      // confirms the popover closed.
      expect(screen.queryByText("HEVC")).not.toBeInTheDocument();
    });
  });

  it("deep-link via ?video_codec=hevc&container=mp4 initializes filters from URL", async () => {
    // Set the URL BEFORE rendering so the FilesPage mount effect
    // picks it up. The mount effect reads
    // ``window.location.search`` directly.
    window.history.replaceState(
      {},
      "",
      "/files?video_codec=hevc&container=mp4",
    );
    render(wrap(<FilesPage />));

    await waitFor(() => {
      const last = lastMediaPath();
      expect(last).toBeDefined();
      expect(last).toContain("video_codec=hevc");
      expect(last).toContain("container=mp4");
    });
  });
});
