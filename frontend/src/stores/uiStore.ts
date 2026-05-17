import { create } from "zustand";
import { persist } from "zustand/middleware";

import { applyAccent, applyTheme, type AccentName } from "@/lib/accent";

export type Theme = "light" | "dark";
export type NavLayout = "sidebar" | "top";
export type RulesBuilderStyle = "form" | "visual";

/**
 * Stage 13 (plan §606) — canonical list of dashboard card
 * keys. The order here is the DEFAULT order operators see on
 * a fresh install / before they reorder anything.
 *
 * Used by:
 *   * The persist ``migrate`` callback (addendum B.10) to
 *     populate ``dashboardOrder`` for existing operators who
 *     don't have the key in their persisted state yet.
 *   * The DashboardPage card-rail rendering loop.
 *   * The card-reorder test to assert default semantics.
 *
 * If a future stage adds a new card, append the key here.
 * Existing operators' persisted ``dashboardOrder`` will not
 * contain the new key; the DashboardPage renderer falls
 * back to "render any card from this list that isn't in
 * dashboardOrder" so the new card surfaces by default.
 */
export const DASHBOARD_CARD_KEYS = [
  "severity",
  "libraries",
  "integrations",
  "categories",
  "live_now",
  "top-rules",
  "suggestions",
  "recent-scans",
  "recent-jobs",
] as const;

export type DashboardCardKey = (typeof DASHBOARD_CARD_KEYS)[number];

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
  /**
   * Stage 13 (plan §606) — operator-defined card order. When
   * empty (fresh install or pre-Stage-13 persisted state),
   * the page renders ``DASHBOARD_CARD_KEYS`` directly. The
   * persist migrate callback below populates this on first
   * rehydrate so existing operators don't see their layout
   * jump.
   */
  dashboardOrder: string[];
  /**
   * Stage 13 (plan §606) — explicit "disabled" column. A
   * card in this list is hidden from the main grid and
   * appears in the collapsible "Disabled cards" rail.
   * Semantically different from ``dashboardHidden`` (which
   * is the existing "collapsed but still in the grid"
   * concept) — the rail surface is for cards the operator
   * has actively removed from their dashboard.
   */
  dashboardDisabled: string[];
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
  /** Stage 13 — move a card to the disabled rail. */
  disableDashboardCard: (key: string) => void;
  /** Stage 13 — restore a disabled card to the active grid. */
  enableDashboardCard: (key: string) => void;
  /** Stage 13 — replace ``oldKey`` with ``newKey``: ``oldKey``
   *  goes to the disabled rail and ``newKey`` takes its
   *  position in ``dashboardOrder``. If ``newKey`` was
   *  already in the order, the two cards swap positions
   *  instead. */
  replaceDashboardCard: (oldKey: string, newKey: string) => void;
}

const DEFAULTS: UiPreferences = {
  theme: "light",
  accent: "indigo",
  nav: "sidebar",
  rulesBuilder: "form",
  dashboardHidden: [],
  // Stage 13: default to the canonical order. The persist
  // migrate callback below also sets this when an existing
  // operator's persisted state is missing the key — addendum
  // B.10 mandates we don't break their layout.
  dashboardOrder: [...DASHBOARD_CARD_KEYS],
  dashboardDisabled: [],
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
      resetDashboardLayout: () =>
        set({
          dashboardHidden: [],
          dashboardOrder: [...DASHBOARD_CARD_KEYS],
          dashboardDisabled: [],
        }),
      disableDashboardCard: (key) => {
        const state = get();
        const order = state.dashboardOrder.filter((k) => k !== key);
        const disabled = state.dashboardDisabled.includes(key)
          ? state.dashboardDisabled
          : [...state.dashboardDisabled, key];
        set({ dashboardOrder: order, dashboardDisabled: disabled });
      },
      enableDashboardCard: (key) => {
        const state = get();
        const disabled = state.dashboardDisabled.filter((k) => k !== key);
        const order = state.dashboardOrder.includes(key)
          ? state.dashboardOrder
          : [...state.dashboardOrder, key];
        set({ dashboardOrder: order, dashboardDisabled: disabled });
      },
      replaceDashboardCard: (oldKey, newKey) => {
        const state = get();
        if (oldKey === newKey) return;

        const order = [...state.dashboardOrder];
        const disabled = [...state.dashboardDisabled];

        const oldIdx = order.indexOf(oldKey);
        if (oldIdx === -1) return; // nothing to replace.

        const newIdxInOrder = order.indexOf(newKey);
        if (newIdxInOrder !== -1) {
          // Both currently in the grid → swap positions.
          order[oldIdx] = newKey;
          order[newIdxInOrder] = oldKey;
          set({ dashboardOrder: order });
          return;
        }

        // ``newKey`` is coming from the disabled rail.
        // Replace ``oldKey`` with ``newKey`` in the order;
        // ``oldKey`` moves to the rail.
        order[oldIdx] = newKey;
        const newDisabled = disabled.filter((k) => k !== newKey);
        if (!newDisabled.includes(oldKey)) newDisabled.push(oldKey);
        set({
          dashboardOrder: order,
          dashboardDisabled: newDisabled,
        });
      },
    }),
    {
      name: "auditarr.ui",
      // Stage 13 (addendum B.10) — version bump from 0 to 1.
      // Existing persisted state may be missing ``dashboardOrder``
      // and ``dashboardDisabled``. The migrate callback fills
      // them in based on the operator's current state so their
      // layout doesn't jump.
      version: 1,
      migrate: (persistedState: unknown, fromVersion: number) => {
        const s = (persistedState ?? {}) as Partial<UiPreferences>;
        if (fromVersion < 1) {
          // Compute the order from current visible cards.
          // Visible = every canonical card NOT in
          // ``dashboardHidden``. Existing operators see their
          // collapsed sections preserved AND a sensible
          // default order matching what they're already
          // looking at.
          const hidden = new Set(s.dashboardHidden ?? []);
          const visible = DASHBOARD_CARD_KEYS.filter(
            (k) => !hidden.has(k),
          );
          // Cards in ``hidden`` are still in the grid (just
          // collapsed). They should also be in
          // ``dashboardOrder`` — collapse and disable are
          // different concepts. Re-add them at the end so
          // the operator-visible cards stay first.
          const collapsedAtEnd = DASHBOARD_CARD_KEYS.filter((k) =>
            hidden.has(k),
          );
          return {
            ...s,
            dashboardOrder: s.dashboardOrder ?? [
              ...visible,
              ...collapsedAtEnd,
            ],
            dashboardDisabled: s.dashboardDisabled ?? [],
          };
        }
        return s;
      },
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
