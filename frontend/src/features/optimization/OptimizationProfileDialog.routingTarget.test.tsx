/**
 * Stage 07 (v1.7) — OptimizationProfileDialog routing-target test.
 *
 * Plan §416:
 *     Switch routing target; assert irrelevant controls disappear.
 *
 * This test pins the ``OPTIONS_BY_TARGET`` gating contract:
 *
 *   - When ``routing_target=in_process`` (default), every encoding
 *     knob renders — CRF, Preset, Max bitrate, Scale, Extra args.
 *   - When ``routing_target=plex`` or ``jellyfin``, the in-process-
 *     only knobs (Preset, Max bitrate, Scale, Extra args) disappear;
 *     CRF is rendered but disabled with a hint that the provider
 *     ignores it.
 *   - When ``routing_target=tdarr``, Scale stays (Tdarr accepts
 *     scale hints) but Preset / Max bitrate / Extra args go away.
 *   - The transcode_scope select is always present and writes the
 *     correct field on change.
 *   - The acknowledged_destructive ... wait that's Stage 06. The
 *     Stage 07 ack here is the schedule_window toggle. Test that
 *     enabling it shows the start/end/timezone controls and that
 *     a tz mismatch shows the warning.
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
const apiPatch = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
    patch: (path: string, body?: unknown) => apiPatch(path, body),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
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
    user: { id: "u1", username: "admin", role: "admin" },
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

import { OptimizationProfileDialog } from "@/features/optimization/OptimizationProfileDialog";

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
  apiPatch.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/integrations") {
      return [];
    }
    return null;
  });
  apiPost.mockResolvedValue({});
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderDialog(): void {
  render(
    wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
  );
}

function selectRoutingTarget(value: string): void {
  const select = screen.getByTestId(
    "routing-target-select",
  ) as HTMLSelectElement;
  fireEvent.change(select, { target: { value } });
}

// ── 1. Default in_process exposes every knob ───────────────────

describe("Stage 07 — routing_target default in_process", () => {
  it("exposes every encoding knob", async () => {
    renderDialog();
    // The Stage 07 fieldset is present at mount.
    expect(screen.getByTestId("stage07-routing-fieldset")).toBeInTheDocument();
    // Default value.
    const select = screen.getByTestId(
      "routing-target-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("in_process");

    // Video knobs all visible (CRF is rendered via a slider with
    // aria-label "CRF (constant rate factor)").
    expect(
      screen.getByLabelText(/CRF \(constant rate factor\)/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/preset/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/max bitrate/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^scale$/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText(/extra ffmpeg arguments/i),
    ).toBeInTheDocument();
  });
});

// ── 2. Switching to plex hides in-process-only knobs ───────────

describe("Stage 07 — routing_target=plex", () => {
  it("hides Preset, Max bitrate, Scale, Extra args; keeps CRF (disabled with hint)", async () => {
    renderDialog();
    selectRoutingTarget("plex");

    await waitFor(() => {
      const select = screen.getByTestId(
        "routing-target-select",
      ) as HTMLSelectElement;
      expect(select.value).toBe("plex");
    });

    // Hidden knobs:
    expect(screen.queryByLabelText(/preset/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/max bitrate/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^scale$/i)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/extra ffmpeg arguments/i),
    ).not.toBeInTheDocument();

    // CRF is still rendered (the slider stays so operators see
    // the value), but the form copy notes the provider ignores it.
    const crfSlider = screen.getByLabelText(/CRF \(constant rate factor\)/i);
    expect(crfSlider).toBeInTheDocument();
    expect(crfSlider).toBeDisabled();
    // The hint text appears in the surrounding span.
    expect(
      screen.getByText(/routing target ignores CRF/i),
    ).toBeInTheDocument();
  });
});

// ── 3. Switching to jellyfin matches plex's mask ───────────────

describe("Stage 07 — routing_target=jellyfin", () => {
  it("hides the same set of knobs as plex", async () => {
    renderDialog();
    selectRoutingTarget("jellyfin");

    await waitFor(() => {
      expect(screen.queryByLabelText(/preset/i)).not.toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/max bitrate/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^scale$/i)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/extra ffmpeg arguments/i),
    ).not.toBeInTheDocument();
  });
});

// ── 4. Switching to tdarr preserves scale_height ───────────────

describe("Stage 07 — routing_target=tdarr", () => {
  it("hides Preset / Max bitrate / Extra args but KEEPS Scale", async () => {
    renderDialog();
    selectRoutingTarget("tdarr");

    await waitFor(() => {
      expect(screen.queryByLabelText(/preset/i)).not.toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/max bitrate/i)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/extra ffmpeg arguments/i),
    ).not.toBeInTheDocument();
    // Scale stays — Tdarr accepts scale hints.
    expect(screen.getByLabelText(/^scale$/i)).toBeInTheDocument();
  });
});

// ── 5. Switching back restores everything ──────────────────────

describe("Stage 07 — routing_target round-trip", () => {
  it("re-exposes the in-process-only knobs after returning to in_process", async () => {
    renderDialog();
    selectRoutingTarget("plex");
    await waitFor(() => {
      expect(screen.queryByLabelText(/preset/i)).not.toBeInTheDocument();
    });
    selectRoutingTarget("in_process");
    await waitFor(() => {
      expect(screen.getByLabelText(/preset/i)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/max bitrate/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^scale$/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText(/extra ffmpeg arguments/i),
    ).toBeInTheDocument();
  });
});

// ── 6. transcode_scope select writes the field ─────────────────

describe("Stage 07 — transcode_scope", () => {
  it("renders the three options and changes propagate on submit", async () => {
    renderDialog();
    const select = screen.getByTestId(
      "transcode-scope-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("video_and_audio");
    // Pick video_only.
    fireEvent.change(select, { target: { value: "video_only" } });
    await waitFor(() => {
      expect(select.value).toBe("video_only");
    });

    // Submit and confirm the body carries the right field.
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "Video-only profile" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    expect(settings.transcode_scope).toBe("video_only");
  });
});

// ── 7. Schedule window controls ────────────────────────────────

describe("Stage 07 — schedule_window", () => {
  it("hides start/end/timezone inputs when the toggle is off", () => {
    renderDialog();
    expect(
      screen.queryByTestId("schedule-window-controls"),
    ).not.toBeInTheDocument();
  });

  it("shows start/end/timezone inputs when the toggle is on", async () => {
    renderDialog();
    const toggle = screen.getByLabelText(/restrict to a daily schedule window/i);
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(
        screen.getByTestId("schedule-window-controls"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("schedule-start-hour")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-end-hour")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-timezone")).toBeInTheDocument();
  });

  it("schedule window persists on submit", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "Night-time profile" },
    });
    const toggle = screen.getByLabelText(/restrict to a daily schedule window/i);
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(
        screen.getByTestId("schedule-window-controls"),
      ).toBeInTheDocument();
    });
    // Change end hour to a known value.
    fireEvent.change(screen.getByTestId("schedule-end-hour"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    expect(settings.schedule_window).toBeDefined();
    const win = settings.schedule_window as Record<string, unknown>;
    expect(win.end_hour).toBe(5);
    // start_hour defaulted to 22 per the toggle seed.
    expect(win.start_hour).toBe(22);
    // timezone defaulted to whatever the browser reports.
    expect(typeof win.timezone).toBe("string");
  });
});

// ── 8. Tag scope writes a comma-separated list ─────────────────

describe("Stage 07 — tag_scope", () => {
  it("comma-separated input parses + dedups on submit", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "Tagged profile" },
    });
    fireEvent.change(screen.getByTestId("tag-scope-input"), {
      target: { value: "plex-incompatible-video, 4k, plex-incompatible-video" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    // Dedup preserves first occurrence order.
    expect(settings.tag_scope).toEqual(["plex-incompatible-video", "4k"]);
  });

  it("empty tag_scope is omitted from the submitted settings", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "No-tag profile" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    // Backend convention: empty list = key absent so the JSON
    // view stays tidy for the common case.
    expect(settings.tag_scope).toBeUndefined();
  });
});
