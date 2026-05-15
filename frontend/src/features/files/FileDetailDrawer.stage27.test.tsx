/**
 * Stage 27 — FileDetailDrawer reprobe / quarantine / restore tests.
 *
 * Three operator paths to pin:
 *
 *   - Re-probe button POSTs to /media/{id}/reprobe
 *   - Quarantine button (when file is not quarantined) prompts and
 *     POSTs to /media/{id}/quarantine
 *   - Restore button (when file IS quarantined) POSTs to
 *     /media/{id}/unquarantine; the head shows the existing reason
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

import { FileDetailDrawer } from "@/features/files/FileDetailDrawer";

const SUMMARY = {
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

const DETAIL = {
  ...SUMMARY,
  duration_seconds: 3600,
  bitrate_kbps: 8000,
  subtitle_codec: null,
  framerate: 23.976,
  subtitle_languages: null,
  audio_languages: ["eng"],
  probe: null,
  probe_failed: false,
  probe_error: null,
  last_scan_id: "scan-1",
  seen_at: "2026-05-10T12:00:00Z",
  quarantined_at: null,
  quarantined_reason: null,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-10T12:00:00Z",
};

const DETAIL_QUARANTINED = {
  ...DETAIL,
  quarantined: true,
  quarantined_at: "2026-05-11T10:00:00Z",
  quarantined_reason: "Bad encode; investigate later",
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
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("FileDetailDrawer Stage 27", () => {
  it("Re-probe button POSTs to /media/{id}/reprobe", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === `/media/${SUMMARY.id}`) return DETAIL;
      if (path === `/media/${SUMMARY.id}/evaluations`) return [];
      return null;
    });
    apiPost.mockResolvedValue(DETAIL);

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => undefined} />));

    const btn = await screen.findByRole("button", { name: /re-probe/i });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        `/media/${SUMMARY.id}/reprobe`,
        undefined,
      );
    });
  });

  it("Quarantine button prompts and POSTs to /quarantine", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === `/media/${SUMMARY.id}`) return DETAIL;
      if (path === `/media/${SUMMARY.id}/evaluations`) return [];
      return null;
    });
    apiPost.mockResolvedValue(DETAIL_QUARANTINED);

    const originalPrompt = window.prompt;
    window.prompt = vi.fn(() => "reason text") as unknown as typeof window.prompt;

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => undefined} />));

    const btn = await screen.findByRole("button", { name: /^quarantine$/i });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        `/media/${SUMMARY.id}/quarantine`,
        expect.objectContaining({ reason: "reason text" }),
      );
    });

    window.prompt = originalPrompt;
  });

  it("when file is quarantined, head shows reason and foot shows Restore", async () => {
    const summaryQ = { ...SUMMARY, quarantined: true };
    apiGet.mockImplementation(async (path: string) => {
      if (path === `/media/${SUMMARY.id}`) return DETAIL_QUARANTINED;
      if (path === `/media/${SUMMARY.id}/evaluations`) return [];
      return null;
    });

    render(
      wrap(<FileDetailDrawer file={summaryQ} onClose={() => undefined} />),
    );

    // Wait for the detail to load so the reason renders.
    await waitFor(() =>
      expect(screen.getByText(/quarantine reason/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Bad encode; investigate later/i),
    ).toBeInTheDocument();

    // The foot shows Restore (not Quarantine) when already quarantined.
    expect(
      screen.getByRole("button", { name: /restore/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^quarantine$/i }),
    ).not.toBeInTheDocument();
  });

  it("Restore button POSTs to /media/{id}/unquarantine", async () => {
    const summaryQ = { ...SUMMARY, quarantined: true };
    apiGet.mockImplementation(async (path: string) => {
      if (path === `/media/${SUMMARY.id}`) return DETAIL_QUARANTINED;
      if (path === `/media/${SUMMARY.id}/evaluations`) return [];
      return null;
    });
    apiPost.mockResolvedValue(DETAIL);

    render(
      wrap(<FileDetailDrawer file={summaryQ} onClose={() => undefined} />),
    );

    const restore = await screen.findByRole("button", { name: /restore/i });
    fireEvent.click(restore);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        `/media/${SUMMARY.id}/unquarantine`,
        undefined,
      );
    });
  });

  it("Quarantine prompt cancellation aborts the action", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === `/media/${SUMMARY.id}`) return DETAIL;
      if (path === `/media/${SUMMARY.id}/evaluations`) return [];
      return null;
    });
    const originalPrompt = window.prompt;
    window.prompt = vi.fn(() => null) as unknown as typeof window.prompt;

    render(wrap(<FileDetailDrawer file={SUMMARY} onClose={() => undefined} />));

    const btn = await screen.findByRole("button", { name: /^quarantine$/i });
    fireEvent.click(btn);

    // No POST issued for quarantine.
    await waitFor(() => {
      const calls = apiPost.mock.calls.map(([p]) => p);
      expect(calls.filter((p) => String(p).includes("quarantine"))).toHaveLength(0);
    });

    window.prompt = originalPrompt;
  });
});
