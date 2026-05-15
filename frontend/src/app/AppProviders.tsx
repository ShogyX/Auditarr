import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";

import { ErrorBoundary } from "@/components/shell/ErrorBoundary";
import { useAuthStore } from "@/stores/authStore";

interface ProvidersProps {
  children: ReactNode;
}

export function AppProviders({ children }: ProvidersProps) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  // Block first paint until persisted auth state has rehydrated. Without
  // this, ``RequireAuth`` sees ``tokens === null`` for a frame and bounces
  // an authenticated user to /login on hard reload.
  const isHydrated = useAuthStore((s) => s.isHydrated);
  useEffect(() => {
    // ``persist`` triggers ``onRehydrateStorage`` synchronously when there is
    // nothing to load, so flag hydration immediately as a fallback.
    if (!useAuthStore.persist.hasHydrated()) return;
    useAuthStore.getState().hydrate();
  }, []);

  if (!isHydrated) {
    return <div className="min-h-screen flex items-center justify-center bg-bg" />;
  }

  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>{children}</BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
