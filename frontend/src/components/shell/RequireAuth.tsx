import { Navigate, useLocation } from "react-router-dom";
import type { ReactElement } from "react";

import { useAuthStore } from "@/stores/authStore";

interface RequireAuthProps {
  children: ReactElement;
}

export function RequireAuth({ children }: RequireAuthProps) {
  const tokens = useAuthStore((s) => s.tokens);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const location = useLocation();

  if (!isHydrated) return null;

  if (!tokens?.accessToken) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return children;
}
