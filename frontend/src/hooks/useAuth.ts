import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";
import { useAuthStore, type AuthTokens, type AuthUser } from "@/stores/authStore";

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

interface LoginRequest {
  login: string;
  password: string;
}

interface RegisterRequest {
  email: string;
  username: string;
  password: string;
  full_name?: string;
}

function tokensFromResponse(r: TokenResponse): AuthTokens {
  return {
    accessToken: r.access_token,
    refreshToken: r.refresh_token,
    expiresAt: Date.now() + r.expires_in * 1000,
  };
}

export function useLogin() {
  const setSession = useAuthStore((s) => s.setSession);
  return useMutation({
    mutationKey: ["auth", "login"],
    mutationFn: async (body: LoginRequest) => {
      const tokens = tokensFromResponse(await apiClient.post<TokenResponse>("/auth/login", body));
      // Eagerly fetch the user record so the shell has it before navigation.
      useAuthStore.getState().setTokens(tokens);
      const user = await apiClient.get<AuthUser>("/auth/me");
      setSession(user, tokens);
      return user;
    },
  });
}

export function useRegister() {
  return useMutation({
    mutationKey: ["auth", "register"],
    mutationFn: (body: RegisterRequest) => apiClient.post<AuthUser>("/auth/register", body),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const clear = useAuthStore((s) => s.clear);
  return useMutation({
    mutationKey: ["auth", "logout"],
    mutationFn: async () => {
      const tokens = useAuthStore.getState().tokens;
      if (tokens?.refreshToken) {
        try {
          await apiClient.post("/auth/logout", {
            refresh_token: tokens.refreshToken,
          });
        } catch {
          // Even if logout fails on the server, drop local session.
        }
      }
      clear();
      queryClient.clear();
    },
  });
}

export function useCurrentUser() {
  const tokens = useAuthStore((s) => s.tokens);
  const setUser = useAuthStore((s) => s.setUser);
  return useQuery({
    queryKey: ["auth", "me"],
    enabled: !!tokens?.accessToken,
    queryFn: async () => {
      const user = await apiClient.get<AuthUser>("/auth/me");
      setUser(user);
      return user;
    },
  });
}

export function useRequestPasswordReset() {
  return useMutation({
    mutationKey: ["auth", "request-reset"],
    mutationFn: (email: string) => apiClient.post("/auth/password/reset/request", { email }),
  });
}

export function useConfirmPasswordReset() {
  return useMutation({
    mutationKey: ["auth", "confirm-reset"],
    mutationFn: (body: { token: string; new_password: string }) =>
      apiClient.post("/auth/password/reset/confirm", body),
  });
}

export function useChangePassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: ["auth", "change-password"],
    mutationFn: (body: { current_password: string; new_password: string }) =>
      apiClient.post("/auth/password/change", body),
    // Stage 13 (plan §604) — change-password bumps the
    // server-side token version and (Stage 12) may clear
    // the must_change_password flag. Both are visible via
    // ``/auth/me`` so invalidate the auth-kind graph to
    // refresh any current-user-derived query.
    onSuccess: () => invalidateRelated(qc, "auth"),
  });
}

// Stage 5 (audit follow-up): edit your own profile (display name,
// email). Backend already supports this — the hook was missing.
export function useUpdateProfile() {
  const qc = useQueryClient();
  const setUser = useAuthStore((s) => s.setUser);
  return useMutation({
    mutationKey: ["auth", "update-profile"],
    mutationFn: (body: { full_name?: string; email?: string }) =>
      apiClient.patch<AuthUser>("/auth/me", body),
    onSuccess: (user) => {
      // Stage 13 (plan §604) — update the auth store
      // AND invalidate the auth-kind graph in case any
      // other query has cached the user's display info.
      setUser(user);
      invalidateRelated(qc, "auth");
    },
  });
}

// Stage 5 (audit follow-up): revoke every other active session
// (the current one survives). Useful when an operator suspects
// a token leaked.
export function useLogoutAll() {
  return useMutation({
    mutationKey: ["auth", "logout-all"],
    mutationFn: () => apiClient.post("/auth/logout-all", {}),
  });
}
