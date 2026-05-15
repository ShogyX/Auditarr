import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  is_verified: boolean;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  /** Unix epoch (ms) when the access token expires. */
  expiresAt: number;
}

interface AuthState {
  user: AuthUser | null;
  tokens: AuthTokens | null;
  isHydrated: boolean;

  setSession: (user: AuthUser, tokens: AuthTokens) => void;
  setUser: (user: AuthUser | null) => void;
  setTokens: (tokens: AuthTokens | null) => void;
  clear: () => void;
  hydrate: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      tokens: null,
      isHydrated: false,

      setSession: (user, tokens) => set({ user, tokens }),
      setUser: (user) => set({ user }),
      setTokens: (tokens) => set({ tokens }),
      clear: () => set({ user: null, tokens: null }),
      hydrate: () => set({ isHydrated: true }),
    }),
    {
      name: "auditarr.auth",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ user: state.user, tokens: state.tokens }),
      onRehydrateStorage: () => (state) => {
        state?.hydrate();
      },
    },
  ),
);
