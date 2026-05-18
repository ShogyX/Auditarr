/**
 * v1.9 Stage 2.5 — dashboard-card disable wiring.
 *
 * Two contracts pinned:
 *
 *   1. ``useDashboardCardDisabled(key)`` returns a tuple whose first
 *      slot reflects the store and whose setter routes to the
 *      store's ``disableDashboardCard`` / ``enableDashboardCard``
 *      actions. Setting twice for the same value is idempotent.
 *
 *   2. Every key in ``DASHBOARD_CARD_KEYS`` has a working disable
 *      gate — toggling ``setDisabled(true)`` for any key removes
 *      the card's identifier from ``dashboardOrder`` and adds it
 *      to ``dashboardDisabled``. Pre-1.9 four keys (libraries,
 *      integrations, top-rules, recent-*) weren't gated at the
 *      render layer; this test sanity-checks the store-level
 *      contract that the gates depend on.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useDashboardCardDisabled } from "@/hooks/useDashboardCardDisabled";
import {
  DASHBOARD_CARD_KEYS,
  useUiStore,
  type DashboardCardKey,
} from "@/stores/uiStore";

function resetStore(): void {
  // Replace state with a fresh defaults snapshot so each test
  // starts from a known baseline.
  useUiStore.setState({
    dashboardOrder: [...DASHBOARD_CARD_KEYS],
    dashboardDisabled: [],
  });
}

beforeEach(() => {
  resetStore();
});

afterEach(() => {
  resetStore();
});

describe("v1.9 Stage 2.5 — useDashboardCardDisabled", () => {
  it("starts with isDisabled=false for every default card key", () => {
    for (const key of DASHBOARD_CARD_KEYS) {
      const { result } = renderHook(() =>
        useDashboardCardDisabled(key as DashboardCardKey),
      );
      const [isDisabled] = result.current;
      expect(isDisabled, `key ${key} unexpectedly disabled`).toBe(false);
    }
  });

  it("setDisabled(true) flips isDisabled and updates the store", () => {
    const { result, rerender } = renderHook(() =>
      useDashboardCardDisabled("severity"),
    );
    act(() => {
      const [, setDisabled] = result.current;
      setDisabled(true);
    });
    rerender();
    const [isDisabled] = result.current;
    expect(isDisabled).toBe(true);

    const state = useUiStore.getState();
    expect(state.dashboardDisabled).toContain("severity");
    expect(state.dashboardOrder).not.toContain("severity");
  });

  it("setDisabled(false) re-enables a disabled card", () => {
    // Seed: already disabled.
    act(() => {
      useUiStore.getState().disableDashboardCard("categories");
    });

    const { result, rerender } = renderHook(() =>
      useDashboardCardDisabled("categories"),
    );
    expect(result.current[0]).toBe(true);

    act(() => {
      const [, setDisabled] = result.current;
      setDisabled(false);
    });
    rerender();
    expect(result.current[0]).toBe(false);

    const state = useUiStore.getState();
    expect(state.dashboardDisabled).not.toContain("categories");
    expect(state.dashboardOrder).toContain("categories");
  });

  it("setDisabled(true) twice is idempotent — no duplicate entry", () => {
    const { result, rerender } = renderHook(() =>
      useDashboardCardDisabled("live_now"),
    );
    act(() => {
      result.current[1](true);
    });
    rerender();
    act(() => {
      result.current[1](true);
    });
    rerender();
    expect(
      useUiStore
        .getState()
        .dashboardDisabled.filter((k) => k === "live_now").length,
    ).toBe(1);
  });

  it("each card key can be toggled off and back on independently", () => {
    // Walk every key the dashboard knows about. The previously-
    // missing gates (libraries, integrations, top-rules, recent-*)
    // depend on this contract holding — if a key silently fails
    // here, the card on the page won't toggle either.
    for (const key of DASHBOARD_CARD_KEYS) {
      const { result, rerender } = renderHook(() =>
        useDashboardCardDisabled(key as DashboardCardKey),
      );
      // Off → on.
      act(() => {
        result.current[1](true);
      });
      rerender();
      expect(result.current[0], `enable failed for ${key}`).toBe(true);
      // On → off.
      act(() => {
        result.current[1](false);
      });
      rerender();
      expect(result.current[0], `disable failed for ${key}`).toBe(false);
    }
  });
});
