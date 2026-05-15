import { AppProviders } from "@/app/AppProviders";
import { AppRoutes } from "@/app/AppRoutes";

export function App() {
  return (
    <AppProviders>
      <AppRoutes />
    </AppProviders>
  );
}
