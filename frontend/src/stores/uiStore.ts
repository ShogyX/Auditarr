import { create } from "zustand";
import { persist } from "zustand/middleware";

import { applyAccent, applyTheme, type AccentName } from "@/lib/accent";

export type Theme = "light" | "dark";
export type NavLayout = "sidebar" | "top";
export type RulesBuilderStyle = "form" | "visual";

export interface UiPreferences {
  theme: Theme;
  accent: AccentName;
  nav: NavLayout;
  rulesBuilder: RulesBuilderStyle;
  /**
   * Stage 11 audit fix (Issue 16): keys of dashboard sections the
   * operator has collapsed. Stored as ``string[]`` rather than
   * ``Set<string>`` because zustand/persist serializes the store
   * to JSON and Sets become empty objects in transit. Helpers on
   * the store keep the semantic Set-like (toggle / reset).
   */
  dashboardHidden: string[];
}

interface UiStore extends UiPreferences {
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  setAccent: (accent: AccentName) => void;
  setNav: (nav: NavLayout) => void;
  setRulesBuilder: (style: RulesBuilderStyle) => void;
  setMany: (patch: Partial<UiPreferences>) => void;
  /** Toggle a dashboard section's collapsed/expanded state. */
  toggleDashboardSection: (key: string) => void;
  /** Restore every dashboard section to expanded. */
  resetDashboardLayout: () => void;
}

const DEFAULTS: UiPreferences = {
  theme: "light",
  accent: "indigo",
  nav: "sidebar",
  rulesBuilder: "form",
  dashboardHidden: [],
};

export const useUiStore = create<UiStore>()(
  persist(
    (set, get) => ({
      ...DEFAULTS,
      setTheme: (theme) => {
        applyTheme(theme);
        applyAccent(get().accent, theme);
        set({ theme });
      },
      toggleTheme: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
      setAccent: (accent) => {
        applyAccent(accent, get().theme);
        set({ accent });
      },
      setNav: (nav) => set({ nav }),
      setRulesBuilder: (rulesBuilder) => set({ rulesBuilder }),
      setMany: (patch) => {
        const next = { ...get(), ...patch };
        applyTheme(next.theme);
        applyAccent(next.accent, next.theme);
        set(patch);
      },
      toggleDashboardSection: (key) => {
        const current = get().dashboardHidden;
        const next = current.includes(key)
          ? current.filter((k) => k !== key)
          : [...current, key];
        set({ dashboardHidden: next });
      },
      resetDashboardLayout: () => set({ dashboardHidden: [] }),
    }),
    {
      name: "auditarr.ui",
      // Stage 20: re-apply theme + accent to the DOM after rehydrate.
      // Without this, a page reload that reads a persisted dark theme
      // out of localStorage would update the store but leave the
      // <html data-theme=""> attribute unset, and the user would see
      // light tokens until the first manual toggle.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        applyTheme(state.theme);
        applyAccent(state.accent, state.theme);
      },
    },
  ),
);

/** Initialize CSS variables on app boot from the persisted state. */
export function bootstrapUi(): void {
  const { theme, accent } = useUiStore.getState();
  applyTheme(theme);
  applyAccent(accent, theme);
}
