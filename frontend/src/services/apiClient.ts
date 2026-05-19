/** Typed fetch client with bearer auth + automatic refresh on 401. */

import { useAuthStore, type AuthTokens } from "@/stores/authStore";

export interface ApiErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code;
    this.details = payload.details;
    this.requestId = payload.request_id;
  }
}

const API_ROOT = "/api/v1";

interface RefreshResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

class ApiClient {
  private refreshPromise: Promise<AuthTokens | null> | null = null;

  async request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
    // CodeQL alert #1 (js/client-side-request-forgery): the
    // pre-fix branch ``path.startsWith("http") ? path : …``
    // let any caller-controlled value reach ``fetch`` as a full
    // URL. No real caller used the absolute-URL form (every
    // hook in the app prepends a relative ``/...`` path), so
    // the escape hatch was dead code AND an SSRF surface for
    // any future call site that accidentally forwarded a
    // user-controlled string. Drop it: every request now lands
    // under ``API_ROOT``.
    if (path.startsWith("http")) {
      throw new ApiError(0, {
        code: "client_invalid_path",
        message: "apiClient only accepts relative paths under /api/v1",
      });
    }
    const url = `${API_ROOT}${path}`;
    const headers = new Headers(init.headers);
    headers.set("accept", "application/json");
    // Stage 32: only default to JSON content-type for non-FormData
    // bodies. FormData needs the browser to set the multipart
    // boundary automatically; pre-setting content-type would
    // strip it.
    if (
      init.body &&
      !headers.has("content-type") &&
      !(init.body instanceof FormData)
    ) {
      headers.set("content-type", "application/json");
    }
    const tokens = useAuthStore.getState().tokens;
    if (tokens?.accessToken) {
      headers.set("authorization", `Bearer ${tokens.accessToken}`);
    }

    const response = await fetch(url, { ...init, headers, credentials: "include" });
    const isJson = response.headers.get("content-type")?.includes("application/json");

    if (response.status === 401 && retry && tokens?.refreshToken && !path.startsWith("/auth/")) {
      const refreshed = await this.refreshTokens();
      if (refreshed) {
        return this.request<T>(path, init, false);
      }
      // Refresh failed — drop session.
      useAuthStore.getState().clear();
    }

    if (!response.ok) {
      const payload: ApiErrorPayload = isJson
        ? await response.json().catch(() => fallback(response))
        : fallback(response);
      throw new ApiError(response.status, payload);
    }
    if (response.status === 204) return undefined as T;
    return (isJson ? await response.json() : await response.text()) as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "GET" });
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  /** Stage 32: POST a multipart/form-data body (e.g. file uploads).
   *
   * The browser must set ``Content-Type`` itself so the multipart
   * boundary is included; we deliberately don't pass a content-type
   * header. The auth header + refresh-on-401 path still applies. */
  postForm<T>(path: string, formData: FormData): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: formData,
    });
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  delete<T>(path: string, body?: unknown): Promise<T> {
    // v1.9 Stage 2.4 — DELETE with a JSON body. FastAPI accepts
    // request bodies on DELETE; we use that for the media delete
    // endpoint which carries ``remove_from_disk`` + ``reason``.
    // Pre-1.9 callers continue to work since ``body`` is optional.
    return this.request<T>(path, {
      method: "DELETE",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  /** Coalesce concurrent refresh attempts. */
  private async refreshTokens(): Promise<AuthTokens | null> {
    if (this.refreshPromise) return this.refreshPromise;
    const refreshToken = useAuthStore.getState().tokens?.refreshToken;
    if (!refreshToken) return null;

    this.refreshPromise = (async () => {
      try {
        const response = await fetch(`${API_ROOT}/auth/refresh`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "application/json",
          },
          body: JSON.stringify({ refresh_token: refreshToken }),
          credentials: "include",
        });
        if (!response.ok) return null;
        const data = (await response.json()) as RefreshResponse;
        const tokens: AuthTokens = {
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          expiresAt: Date.now() + data.expires_in * 1000,
        };
        useAuthStore.getState().setTokens(tokens);
        return tokens;
      } catch {
        return null;
      } finally {
        this.refreshPromise = null;
      }
    })();

    return this.refreshPromise;
  }
}

function fallback(response: Response): ApiErrorPayload {
  return {
    code: `http_${response.status}`,
    message: response.statusText || "Request failed",
  };
}

export const apiClient = new ApiClient();
