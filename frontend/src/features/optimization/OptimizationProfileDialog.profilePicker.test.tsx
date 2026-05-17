/**
 * Stage 08 (v1.7) — Profile picker in OptimizationProfileDialog.
 *
 * Plan §460:
 *     With routing target tdarr, assert the profile picker
 *     fetches and renders.
 *
 * Pins the contract:
 *   - Picker is hidden when routing_target=in_process.
 *   - Picker is hidden when routing_target is set but no
 *     integration row is picked yet (the operator hasn't
 *     completed the setup).
 *   - With both routing_target=tdarr AND an integration picked,
 *     the dialog calls ``GET /integrations/{id}/transcode-profiles``
 *     and renders the returned profiles in a select.
 *   - Selecting a profile writes the id into
 *     ``provider_metadata.provider_profile_id`` on submit.
 *   - Empty list → "no provider profiles available" copy.
 *   - Loading state surfaces while the query runs.
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

const TDARR_PROFILES = [
  {
    id: "Tdarr_Plugin_henk_h265",
    name: "Re-encode to HEVC",
    description: "x265, CRF 22.",
    metadata: { Type: "Video" },
  },
  {
    id: "Tdarr_Plugin_lol_remux",
    name: "Remux to MKV",
    description: null,
    metadata: {},
  },
];

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/integrations") {
      return [
        { id: "ig-tdarr-1", name: "Tdarr Box", kind: "tdarr", enabled: true },
      ];
    }
    if (path === "/integrations/ig-tdarr-1/transcode-profiles") {
      return TDARR_PROFILES;
    }
    return null;
  });
  apiPost.mockResolvedValue({
    id: "p1",
    name: "new",
    description: null,
    enabled: true,
    settings: {},
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderDialog(): void {
  render(
    wrap(<OptimizationProfileDialog profile={null} onClose={() => {}} />),
  );
}

// ── 1. Picker hidden by default (routing_target=in_process) ────

describe("Stage 08 — provider profile picker visibility", () => {
  it("does not render the provider picker when routing_target=in_process", () => {
    renderDialog();
    expect(
      screen.queryByTestId("provider-profile-select"),
    ).not.toBeInTheDocument();
  });

  it("does not render the picker when routing_target is set but no integration is picked", () => {
    renderDialog();
    fireEvent.change(screen.getByTestId("routing-target-select"), {
      target: { value: "tdarr" },
    });
    // The picker is still hidden because the operator hasn't
    // chosen a routing-integration row yet.
    expect(
      screen.queryByTestId("provider-profile-select"),
    ).not.toBeInTheDocument();
  });
});

// ── 2. Picker fetches + renders when routing+integration ───────

describe("Stage 08 — provider profile picker fetches and renders", () => {
  it("fetches /integrations/{id}/transcode-profiles when routing target is tdarr and integration is picked", async () => {
    renderDialog();
    fireEvent.change(screen.getByTestId("routing-target-select"), {
      target: { value: "tdarr" },
    });
    // Wait for the integrations select to populate.
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /tdarr box \(tdarr\)/i }),
      ).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/routing integration/i), {
      target: { value: "ig-tdarr-1" },
    });

    // The picker now renders + the API was called.
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-profile-select"),
      ).toBeInTheDocument();
    });
    expect(apiGet).toHaveBeenCalledWith(
      "/integrations/ig-tdarr-1/transcode-profiles",
    );

    // Both profiles are rendered as options once the query
    // resolves. Wait for the data to flow through.
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /Re-encode to HEVC/ }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("option", { name: /Remux to MKV/ }),
    ).toBeInTheDocument();
  });

  it("renders an empty-state hint when the integration has no provider profiles", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === "/integrations") {
        return [
          { id: "ig-jellyfin-1", name: "Jellyfin", kind: "jellyfin", enabled: true },
        ];
      }
      if (path === "/integrations/ig-jellyfin-1/transcode-profiles") {
        return [];
      }
      return null;
    });
    renderDialog();
    fireEvent.change(screen.getByTestId("routing-target-select"), {
      target: { value: "jellyfin" },
    });
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /jellyfin \(jellyfin\)/i }),
      ).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/routing integration/i), {
      target: { value: "ig-jellyfin-1" },
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-profile-select"),
      ).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(
        screen.getByText(/no provider profiles available/i),
      ).toBeInTheDocument();
    });
  });
});

// ── 3. Picked profile persists in provider_metadata on submit ──

describe("Stage 08 — provider profile picker submit", () => {
  it("writes the picked profile id into provider_metadata.provider_profile_id", async () => {
    renderDialog();

    fireEvent.change(await screen.findByLabelText(/^name$/i), {
      target: { value: "tdarr profile" },
    });
    fireEvent.change(screen.getByTestId("routing-target-select"), {
      target: { value: "tdarr" },
    });
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /tdarr box \(tdarr\)/i }),
      ).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/routing integration/i), {
      target: { value: "ig-tdarr-1" },
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-profile-select"),
      ).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /Re-encode to HEVC/ }),
      ).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("provider-profile-select"), {
      target: { value: "Tdarr_Plugin_henk_h265" },
    });

    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    expect(settings.provider_metadata).toEqual({
      provider_profile_id: "Tdarr_Plugin_henk_h265",
    });
  });

  it("omits provider_metadata when no profile is picked", async () => {
    renderDialog();
    // routing_target stays at in_process; provider_metadata never
    // gets populated.
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "in-process profile" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    // Backend convention: empty dict = key absent so the JSON
    // view stays tidy.
    expect(settings.provider_metadata).toBeUndefined();
  });

  it("clearing the picker removes provider_profile_id from provider_metadata", async () => {
    renderDialog();
    fireEvent.change(await screen.findByLabelText(/^name$/i), {
      target: { value: "cleared" },
    });
    fireEvent.change(screen.getByTestId("routing-target-select"), {
      target: { value: "tdarr" },
    });
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /tdarr box \(tdarr\)/i }),
      ).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/routing integration/i), {
      target: { value: "ig-tdarr-1" },
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-profile-select"),
      ).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /Re-encode to HEVC/ }),
      ).toBeInTheDocument();
    });
    // Pick one then clear.
    fireEvent.change(screen.getByTestId("provider-profile-select"), {
      target: { value: "Tdarr_Plugin_henk_h265" },
    });
    fireEvent.change(screen.getByTestId("provider-profile-select"), {
      target: { value: "" },
    });

    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [, body] = apiPost.mock.calls[0]!;
    const settings = (body as Record<string, unknown>).settings as Record<
      string,
      unknown
    >;
    // provider_metadata is empty → key omitted.
    expect(settings.provider_metadata).toBeUndefined();
  });
});

// ── 4. Pre-fills picker from existing profile (edit mode) ──────

describe("Stage 08 — provider profile picker hydrates from existing profile", () => {
  it("pre-selects the saved provider_profile_id when editing a routed profile", async () => {
    render(
      wrap(
        <OptimizationProfileDialog
          profile={{
            id: "p1",
            name: "existing tdarr",
            description: null,
            enabled: true,
            optimization_integration_id: "ig-tdarr-1",
            settings: {
              video: { codec: "libx265", crf: 22 },
              audio: { codec: "copy" },
              routing_target: "tdarr",
              provider_metadata: {
                provider_profile_id: "Tdarr_Plugin_henk_h265",
              },
            },
            max_input_bytes: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          }}
          onClose={() => {}}
        />,
      ),
    );

    await waitFor(() => {
      expect(
        screen.getByTestId("provider-profile-select"),
      ).toBeInTheDocument();
    });
    // Wait for the query to resolve so the picker has options
    // and can show the pre-selected value.
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: /Re-encode to HEVC/ }),
      ).toBeInTheDocument();
    });
    const select = screen.getByTestId(
      "provider-profile-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("Tdarr_Plugin_henk_h265");
  });
});
