/**
 * Stage 5 (audit follow-up) — LibraryEditDialog tests.
 *
 * Pins:
 *   - Opens with current values pre-filled.
 *   - Save sends PATCH with only the dirty fields.
 *   - Save with no changes shows a warning toast and does not PATCH.
 *   - Cancel closes without saving.
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

const apiPatch = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: vi.fn(async () => null),
    post: vi.fn(async () => null),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
    patch: (path: string, body?: unknown) => apiPatch(path, body),
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

import { LibraryEditDialog } from "@/features/settings/LibraryEditDialog";
import type { Library } from "@/hooks/useMedia";
import { toast as toastFn } from "@/lib/toast";

const toastMock = toastFn as unknown as ReturnType<typeof vi.fn>;

const LIB: Library = {
  id: "lib-1",
  name: "Movies",
  root_path: "/mnt/media/Movies",
  kind: "movies",
  enabled: true,
  scan_interval_minutes: 60,
  integration_link: null,
  last_scan_at: null,
  last_scan_status: null,
  last_scan_file_count: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
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
  apiPatch.mockReset();
  apiPatch.mockResolvedValue(LIB);
  toastMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 5 — LibraryEditDialog", () => {
  it("renders nothing when library is null", () => {
    render(
      wrap(<LibraryEditDialog library={null} onOpenChange={() => {}} />),
    );
    expect(
      screen.queryByText(/edit library/i),
    ).not.toBeInTheDocument();
  });

  it("pre-fills inputs with the current library values", async () => {
    render(
      wrap(<LibraryEditDialog library={LIB} onOpenChange={() => {}} />),
    );
    const name = (await screen.findByLabelText(/^name$/i)) as HTMLInputElement;
    const rootPath = screen.getByLabelText(/root path/i) as HTMLInputElement;
    expect(name.value).toBe("Movies");
    expect(rootPath.value).toBe("/mnt/media/Movies");
  });

  it("sends PATCH with only the dirty fields when Save is clicked", async () => {
    render(
      wrap(<LibraryEditDialog library={LIB} onOpenChange={() => {}} />),
    );
    const name = (await screen.findByLabelText(/^name$/i)) as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Renamed" } });
    // root_path NOT touched → must not appear in the patch body.

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    const [path, body] = apiPatch.mock.calls[0]!;
    expect(path).toBe("/libraries/lib-1");
    expect(body).toEqual({ name: "Renamed" });
  });

  it("shows a warning toast and does NOT call PATCH when nothing changed", async () => {
    render(
      wrap(<LibraryEditDialog library={LIB} onOpenChange={() => {}} />),
    );
    await screen.findByLabelText(/^name$/i);

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    // No PATCH; warn toast.
    await new Promise((r) => setTimeout(r, 10));
    expect(apiPatch).not.toHaveBeenCalled();
    expect(toastMock).toHaveBeenCalledWith(
      expect.stringMatching(/nothing to save/i),
      "warn",
    );
  });

  it("Cancel button closes the dialog without saving", async () => {
    const onOpenChange = vi.fn();
    render(
      wrap(<LibraryEditDialog library={LIB} onOpenChange={onOpenChange} />),
    );
    await screen.findByLabelText(/^name$/i);

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(apiPatch).not.toHaveBeenCalled();
  });
});
