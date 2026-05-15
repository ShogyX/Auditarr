/**
 * Stage 5 (audit follow-up) — AccountPage tests.
 *
 * Pins the new self-service surface:
 *   - The Profile form submits PATCH /auth/me with display name + email.
 *   - The Password form submits POST /auth/password/change.
 *   - The Sessions card prompts confirmation, then POSTs /auth/logout-all.
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
    user: {
      id: "u1",
      username: "tester",
      role: "user",
      email: "t@example.com",
      full_name: "Test User",
    },
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

import { AccountPage } from "@/features/account/AccountPage";

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
  apiPatch.mockResolvedValue({
    id: "u1",
    username: "tester",
    role: "user",
    email: "new@example.com",
    full_name: "Renamed",
  });
  apiPost.mockResolvedValue({});
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 5 — AccountPage", () => {
  it("submits profile updates via PATCH /auth/me", async () => {
    render(wrap(<AccountPage />));

    const name = await screen.findByLabelText(/display name/i);
    fireEvent.change(name, { target: { value: "Renamed" } });
    const email = screen.getByLabelText(/^email$/i);
    fireEvent.change(email, { target: { value: "new@example.com" } });

    fireEvent.click(screen.getByRole("button", { name: /save profile/i }));

    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    const [path, body] = apiPatch.mock.calls[0]!;
    expect(path).toBe("/auth/me");
    expect(body).toEqual({
      full_name: "Renamed",
      email: "new@example.com",
    });
  });

  it("submits password change via POST /auth/password/change", async () => {
    render(wrap(<AccountPage />));

    const current = screen.getByLabelText(/current password/i);
    const next = screen.getByLabelText(/^new password$/i);
    const confirm = screen.getByLabelText(/confirm new password/i);
    fireEvent.change(current, { target: { value: "oldpassword123" } });
    fireEvent.change(next, { target: { value: "newpassword456" } });
    fireEvent.change(confirm, { target: { value: "newpassword456" } });

    fireEvent.click(
      screen.getByRole("button", { name: /change password/i }),
    );

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [path, body] = apiPost.mock.calls[0]!;
    expect(path).toBe("/auth/password/change");
    expect(body).toEqual({
      current_password: "oldpassword123",
      new_password: "newpassword456",
    });
  });

  it("does not submit when the password confirmation mismatches", async () => {
    render(wrap(<AccountPage />));

    fireEvent.change(screen.getByLabelText(/current password/i), {
      target: { value: "anything-at-all" },
    });
    fireEvent.change(screen.getByLabelText(/^new password$/i), {
      target: { value: "newpassword456" },
    });
    fireEvent.change(screen.getByLabelText(/confirm new password/i), {
      target: { value: "different-value-1" },
    });

    fireEvent.click(
      screen.getByRole("button", { name: /change password/i }),
    );

    // Allow a tick for the handler to run, then assert no POST fired.
    await new Promise((r) => setTimeout(r, 10));
    expect(apiPost).not.toHaveBeenCalledWith(
      "/auth/password/change",
      expect.anything(),
    );
  });

  it("posts /auth/logout-all after confirmation", async () => {
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    render(wrap(<AccountPage />));

    fireEvent.click(
      screen.getByRole("button", { name: /sign out other sessions/i }),
    );

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/auth/logout-all", {}),
    );
    confirmSpy.mockRestore();
  });

  it("does NOT post /auth/logout-all when the user cancels the prompt", async () => {
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(false);
    render(wrap(<AccountPage />));

    fireEvent.click(
      screen.getByRole("button", { name: /sign out other sessions/i }),
    );

    await new Promise((r) => setTimeout(r, 10));
    expect(apiPost).not.toHaveBeenCalledWith("/auth/logout-all", {});
    confirmSpy.mockRestore();
  });
});
