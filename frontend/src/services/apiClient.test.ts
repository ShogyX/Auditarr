import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient, ApiError } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";

const originalFetch = globalThis.fetch;

function makeResponse(status: number, body?: unknown): Response {
  return new Response(body !== undefined ? JSON.stringify(body) : null, {
    status,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
  });
}

describe("apiClient", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.getState().clear();
    useAuthStore.getState().hydrate();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("attaches the Authorization header when a token is present", async () => {
    useAuthStore.getState().setTokens({ accessToken: "abc", refreshToken: "r", expiresAt: 0 });

    const fetchMock = vi.fn(async (_url, init: RequestInit) => {
      const headers = new Headers(init.headers);
      expect(headers.get("authorization")).toBe("Bearer abc");
      return makeResponse(200, { ok: true });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const result = await apiClient.get<{ ok: boolean }>("/system/info");
    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("refreshes tokens on 401 and retries the original request", async () => {
    useAuthStore.getState().setTokens({ accessToken: "old", refreshToken: "rrr", expiresAt: 0 });

    const calls: { url: string; auth: string | null }[] = [];

    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      const u = String(url);
      calls.push({ url: u, auth: headers.get("authorization") });

      if (u.endsWith("/api/v1/auth/refresh")) {
        return makeResponse(200, {
          access_token: "new",
          refresh_token: "newR",
          token_type: "Bearer",
          expires_in: 60,
        });
      }
      if (headers.get("authorization") === "Bearer old") {
        return makeResponse(401, {
          code: "authentication_required",
          message: "expired",
        });
      }
      return makeResponse(200, { ok: true });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const result = await apiClient.get<{ ok: boolean }>("/system/info");
    expect(result.ok).toBe(true);

    // Expect: original (401) → refresh (200) → retry with new token (200).
    expect(calls.length).toBe(3);
    expect(calls[0]?.auth).toBe("Bearer old");
    expect(calls[1]?.url).toContain("/auth/refresh");
    expect(calls[2]?.auth).toBe("Bearer new");

    expect(useAuthStore.getState().tokens?.accessToken).toBe("new");
  });

  it("clears the session if refresh fails", async () => {
    useAuthStore.getState().setTokens({ accessToken: "old", refreshToken: "rrr", expiresAt: 0 });

    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).endsWith("/api/v1/auth/refresh")) {
        return makeResponse(401, { code: "authentication_required", message: "" });
      }
      return makeResponse(401, { code: "authentication_required", message: "" });
    }) as unknown as typeof fetch;

    await expect(apiClient.get("/system/info")).rejects.toBeInstanceOf(ApiError);
    expect(useAuthStore.getState().tokens).toBeNull();
  });

  it("coalesces concurrent 401s into a single refresh request (Stage 10)", async () => {
    // Long-uptime hardening: two requests in flight at once both see
    // a 401 from the expired access token. Without the single-flight
    // guard, each would fire its own /auth/refresh — burning a
    // refresh token round-trip per concurrent request and racing the
    // store update. The expectation: exactly ONE /auth/refresh call
    // is made, both original requests are retried with the new token.
    useAuthStore.getState().setTokens({
      accessToken: "old",
      refreshToken: "rrr",
      expiresAt: 0,
    });

    let refreshCalls = 0;
    let refreshResolve: ((value: Response) => void) | null = null;
    const refreshPromise = new Promise<Response>((resolve) => {
      refreshResolve = resolve;
    });

    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      const u = String(url);

      if (u.endsWith("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        // Hold the refresh in flight so the two concurrent 401s both
        // hit the same in-flight ``refreshPromise`` on the client.
        return refreshPromise;
      }
      if (headers.get("authorization") === "Bearer old") {
        return makeResponse(401, {
          code: "authentication_required",
          message: "expired",
        });
      }
      return makeResponse(200, { ok: true, url: u });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    // Fire two requests concurrently. Both will 401, both will call
    // refreshTokens(). The first call sets `refreshPromise`; the
    // second call sees it and returns the same promise.
    const p1 = apiClient.get<{ ok: boolean }>("/system/info");
    const p2 = apiClient.get<{ ok: boolean }>("/system/version");

    // Yield once so both concurrent requests reach the refresh code
    // path before we release the held refresh.
    await Promise.resolve();
    await Promise.resolve();

    // Release the refresh with new tokens.
    refreshResolve!(
      makeResponse(200, {
        access_token: "new",
        refresh_token: "newR",
        token_type: "Bearer",
        expires_in: 60,
      }),
    );

    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(true);

    // Exactly one refresh call despite two concurrent 401s.
    expect(refreshCalls).toBe(1);
    expect(useAuthStore.getState().tokens?.accessToken).toBe("new");
  });
});
