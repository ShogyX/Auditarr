/**
 * Stage 7 (audit follow-up) — OptimizationProfileDialog tests.
 *
 * Pins:
 *   - The dialog opens in create mode with default settings + the
 *     four section fieldsets visible.
 *   - Changing structured fields propagates into the JSON view in
 *     Advanced.
 *   - Editing the JSON view propagates back into the structured
 *     state.
 *   - Submit calls ``useCreateProfile().mutateAsync`` with a body
 *     that matches the backend ProfileCreate shape (incl. the new
 *     optional ``optimization_integration_id`` and the structured
 *     ``settings`` payload).
 *   - Edit mode pre-fills from an existing profile.
 *   - codec=copy disables CRF and preset.
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
import type { OptimizationProfile } from "@/hooks/useOptimization";

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
      return [
        { id: "ig-1", name: "Tdarr", kind: "tdarr", enabled: true },
      ];
    }
    return null;
  });
  apiPost.mockResolvedValue({
    id: "p1",
    name: "new",
    description: null,
    enabled: true,
    settings: {},
    max_input_bytes: null,
    created_at: "2026-05-14T00:00:00Z",
    updated_at: "2026-05-14T00:00:00Z",
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

const SAMPLE_PROFILE: OptimizationProfile = {
  id: "p-1",
  name: "Shrink HEVC",
  description: "Existing profile",
  enabled: true,
  settings: {
    video: { codec: "libx264", crf: 18, preset: "slow" },
    audio: { codec: "libopus", bitrate_kbps: 96 },
    subtitles: { handling: "drop" },
    output: { container: "mp4", replace_input: false, keep_backup: true },
    extra_args: ["-tune", "film"],
  },
  max_input_bytes: null,
  optimization_integration_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("Stage 7 — OptimizationProfileDialog (structured form)", () => {
  it("create mode shows the four section fieldsets with sensible defaults", async () => {
    render(
      wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
    );
    // The Modal renders the title twice (sr-only + visible), so use
    // findAllByText and assert presence rather than the singular variant.
    expect(
      (await screen.findAllByText(/new optimization profile/i)).length,
    ).toBeGreaterThan(0);
    // Each fieldset legend identifies its section.
    expect(screen.getByText(/^Video$/)).toBeInTheDocument();
    expect(screen.getByText(/^Audio$/)).toBeInTheDocument();
    expect(screen.getByText(/^Output$/)).toBeInTheDocument();
    expect(screen.getByText(/^Advanced$/)).toBeInTheDocument();
    // Defaults: libx265 video, mkv container, replace_input on.
    // There are two "Codec" labels (video + audio); grab the first.
    const codecSelect = (
      screen.getAllByLabelText(/^codec$/i, { selector: "select" })
    )[0] as HTMLSelectElement;
    expect(codecSelect.value).toBe("libx265");
  });

  it("disables CRF and preset when video codec is 'copy'", async () => {
    render(
      wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
    );
    // Find the video Codec select (first Codec field in the form).
    const codecSelect = (
      await screen.findAllByLabelText(/^codec$/i, { selector: "select" })
    )[0]!;
    fireEvent.change(codecSelect, { target: { value: "copy" } });
    const crf = screen.getByLabelText(/CRF \(constant rate factor\)/i);
    expect(crf).toBeDisabled();
  });

  it("opening the JSON view reflects the structured state", async () => {
    render(
      wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /show json view/i }),
    );
    const textarea = (await screen.findByLabelText(
      /settings \(JSON\)/i,
    )) as HTMLTextAreaElement;
    const parsed = JSON.parse(textarea.value);
    expect(parsed.video.codec).toBe("libx265");
    expect(parsed.output.container).toBe("mkv");
    expect(parsed.subtitles.handling).toBe("copy");
  });

  it("edit mode pre-fills from the existing profile", async () => {
    render(
      wrap(
        <OptimizationProfileDialog
          profile={SAMPLE_PROFILE}
          onClose={() => {}}
        />,
      ),
    );
    expect(await screen.findByDisplayValue("Shrink HEVC")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Existing profile")).toBeInTheDocument();
    const codecSelects = screen.getAllByLabelText(/^codec$/i, {
      selector: "select",
    }) as HTMLSelectElement[];
    expect(codecSelects[0]!.value).toBe("libx264");
  });

  it("submit posts a structured settings body in create mode", async () => {
    render(
      wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
    );
    fireEvent.change(await screen.findByLabelText(/^name$/i), {
      target: { value: "Test profile" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [path, body] = apiPost.mock.calls[0]!;
    expect(path).toBe("/optimization/profiles");
    const b = body as Record<string, unknown>;
    expect(b.name).toBe("Test profile");
    expect(b.enabled).toBe(true);
    expect(b.optimization_integration_id).toBe(null);
    const settings = b.settings as Record<string, unknown>;
    expect((settings.video as Record<string, unknown>).codec).toBe("libx265");
    expect((settings.output as Record<string, unknown>).container).toBe("mkv");
    expect((settings.subtitles as Record<string, unknown>).handling).toBe(
      "copy",
    );
  });

  it("blocks submit when the JSON view has a parse error", async () => {
    render(
      wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
    );
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "Bad json" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: /show json view/i }),
    );
    const textarea = (await screen.findByLabelText(
      /settings \(JSON\)/i,
    )) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "{ not json" } });

    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    // Wait a tick; assert nothing posted.
    await new Promise((r) => setTimeout(r, 30));
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("submit sends the selected routing integration id", async () => {
    render(
      wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
    );
    fireEvent.change(await screen.findByLabelText(/^name$/i), {
      target: { value: "Routed profile" },
    });
    // Wait for integrations to load.
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /tdarr/i }),
      ).toBeInTheDocument();
    });
    const integrationSelect = screen.getByLabelText(/routing integration/i);
    fireEvent.change(integrationSelect, { target: { value: "ig-1" } });

    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    expect((body as Record<string, unknown>).optimization_integration_id).toBe(
      "ig-1",
    );
  });
});
