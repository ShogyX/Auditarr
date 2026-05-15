import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/stores/authStore";

describe("authStore", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.getState().clear();
    useAuthStore.getState().hydrate();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("starts empty", () => {
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().tokens).toBeNull();
  });

  it("setSession stores user and tokens", () => {
    useAuthStore.getState().setSession(
      {
        id: "u1",
        email: "u@example.com",
        username: "u",
        full_name: null,
        role: "admin",
        is_active: true,
        is_verified: true,
      },
      { accessToken: "a", refreshToken: "r", expiresAt: Date.now() + 60_000 },
    );
    const state = useAuthStore.getState();
    expect(state.user?.username).toBe("u");
    expect(state.tokens?.accessToken).toBe("a");
  });

  it("clear wipes both user and tokens", () => {
    useAuthStore.getState().setSession(
      {
        id: "u1",
        email: "u@example.com",
        username: "u",
        full_name: null,
        role: "user",
        is_active: true,
        is_verified: true,
      },
      { accessToken: "a", refreshToken: "r", expiresAt: 0 },
    );
    useAuthStore.getState().clear();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().tokens).toBeNull();
  });

  it("setTokens updates tokens without touching user", () => {
    useAuthStore.getState().setSession(
      {
        id: "u1",
        email: "u@example.com",
        username: "u",
        full_name: null,
        role: "user",
        is_active: true,
        is_verified: true,
      },
      { accessToken: "a", refreshToken: "r", expiresAt: 0 },
    );
    useAuthStore.getState().setTokens({ accessToken: "a2", refreshToken: "r2", expiresAt: 1 });
    expect(useAuthStore.getState().user?.username).toBe("u");
    expect(useAuthStore.getState().tokens?.accessToken).toBe("a2");
  });
});
