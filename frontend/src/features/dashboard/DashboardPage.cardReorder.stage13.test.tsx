/**
 * Stage 13 (plan §610) — dashboard card reorder behaviour.
 *
 * Pins:
 *   - Disabling a card moves it to ``dashboardDisabled`` and
 *     drops it from the active ``dashboardOrder``.
 *   - The "Disabled cards" rail surfaces disabled cards
 *     with a Restore action.
 *   - Restoring a card moves it back to ``dashboardOrder``.
 *   - Replacing card A with card B swaps their positions
 *     when both are in the active grid, or moves A to the
 *     rail when B was already in the rail.
 *
 * These tests exercise the store + the small surface that
 * renders the rail. Full DashboardPage integration would
 * require mocking ~10 hooks; the addendum B.10 migrate
 * callback is tested at the store level too.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DashboardDisabledRail } from "@/features/dashboard/DashboardCardMenu";
import { useUiStore, DASHBOARD_CARD_KEYS } from "@/stores/uiStore";

beforeEach(() => {
  // Reset the store to its default shape before each test.
  // The store is module-scoped + persisted; tests need a clean
  // slate or one test's mutations leak into the next.
  useUiStore.setState({
    dashboardOrder: [...DASHBOARD_CARD_KEYS],
    dashboardDisabled: [],
    dashboardHidden: [],
  });
});

afterEach(() => {
  // Clear the persist storage so subsequent test files
  // don't inherit our mutations.
  try {
    localStorage.removeItem("auditarr.ui");
  } catch {
    // localStorage may not exist in some env configs.
  }
});

describe("Stage 13 — dashboard card disable / enable / replace", () => {
  it("disableDashboardCard moves the key from order to disabled", () => {
    act(() => {
      useUiStore.getState().disableDashboardCard("categories");
    });

    const state = useUiStore.getState();
    expect(state.dashboardDisabled).toContain("categories");
    expect(state.dashboardOrder).not.toContain("categories");
  });

  it("enableDashboardCard restores a disabled card to the active order", () => {
    act(() => {
      useUiStore.getState().disableDashboardCard("categories");
    });
    act(() => {
      useUiStore.getState().enableDashboardCard("categories");
    });

    const state = useUiStore.getState();
    expect(state.dashboardDisabled).not.toContain("categories");
    expect(state.dashboardOrder).toContain("categories");
  });

  it("replaceDashboardCard with a disabled card moves the active one to the rail and brings the other in", () => {
    // Set up: disable "categories" first.
    act(() => {
      useUiStore.getState().disableDashboardCard("categories");
    });
    expect(useUiStore.getState().dashboardOrder).toContain("severity");
    expect(useUiStore.getState().dashboardDisabled).toEqual(["categories"]);

    // Replace "severity" with "categories" — severity goes to
    // the rail, categories takes severity's slot.
    act(() => {
      useUiStore.getState().replaceDashboardCard("severity", "categories");
    });

    const state = useUiStore.getState();
    expect(state.dashboardOrder).toContain("categories");
    expect(state.dashboardOrder).not.toContain("severity");
    expect(state.dashboardDisabled).toContain("severity");
    expect(state.dashboardDisabled).not.toContain("categories");
  });

  it("replaceDashboardCard with two active cards swaps positions", () => {
    const initialOrder = [...useUiStore.getState().dashboardOrder];
    const aIdx = initialOrder.indexOf("severity");
    const bIdx = initialOrder.indexOf("integrations");

    act(() => {
      useUiStore.getState().replaceDashboardCard("severity", "integrations");
    });

    const state = useUiStore.getState();
    // Same length, no rail change.
    expect(state.dashboardOrder).toHaveLength(initialOrder.length);
    expect(state.dashboardDisabled).toEqual([]);
    // Positions swapped.
    expect(state.dashboardOrder[aIdx]).toBe("integrations");
    expect(state.dashboardOrder[bIdx]).toBe("severity");
  });

  it("DashboardDisabledRail renders nothing when no cards are disabled", () => {
    render(<DashboardDisabledRail />);
    expect(
      screen.queryByTestId("dashboard-disabled-rail"),
    ).not.toBeInTheDocument();
  });

  it("DashboardDisabledRail lists disabled cards with restore buttons", () => {
    act(() => {
      useUiStore.getState().disableDashboardCard("categories");
      useUiStore.getState().disableDashboardCard("live_now");
    });

    render(<DashboardDisabledRail />);
    expect(
      screen.getByTestId("dashboard-disabled-rail"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-disabled-card-categories"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-disabled-card-live_now"),
    ).toBeInTheDocument();
    // Restore buttons present for both.
    expect(
      screen.getByTestId("dashboard-restore-card-categories"),
    ).toBeInTheDocument();
  });

  it("resetDashboardLayout clears all three lists back to defaults", () => {
    act(() => {
      useUiStore.getState().disableDashboardCard("categories");
      useUiStore.getState().toggleDashboardSection("severity");
    });
    expect(useUiStore.getState().dashboardDisabled).not.toEqual([]);
    expect(useUiStore.getState().dashboardHidden).not.toEqual([]);

    act(() => {
      useUiStore.getState().resetDashboardLayout();
    });
    const state = useUiStore.getState();
    expect(state.dashboardDisabled).toEqual([]);
    expect(state.dashboardHidden).toEqual([]);
    expect(state.dashboardOrder).toEqual([...DASHBOARD_CARD_KEYS]);
  });
});
