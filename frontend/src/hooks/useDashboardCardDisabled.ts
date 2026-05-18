/**
 * v1.9 Stage 2.5 — dashboard-card disable helper.
 *
 * Wraps the ``dashboardDisabled`` array in the UI store with a
 * tuple-returning hook that every dashboard card uses uniformly.
 *
 * Pre-1.9 the predicate was open-coded in three places (LiveNowCard,
 * CategoriesCard, SuggestionsCard) and missing from four others
 * (severity, libraries, integrations, top-rules), which is the
 * "disable-card setting buggy" complaint: some cards honoured the
 * setting, others didn't. The fix is one helper, one rule:
 *
 *   * Every card calls this hook with its ``DASHBOARD_CARD_KEYS``
 *     identifier.
 *   * When ``isDisabled`` is true, the card returns null (or the
 *     parent skips rendering it).
 *   * The ``setDisabled`` setter routes to the store's
 *     ``disableDashboardCard`` / ``enableDashboardCard`` actions
 *     so the rail in DashboardCardMenu picks up the change with
 *     no extra wiring.
 *
 * Cards that previously read ``s.dashboardDisabled.includes(KEY)``
 * directly should switch to this hook; the tuple shape mirrors
 * ``useState`` so the migration is mechanical.
 */

import { useUiStore } from "@/stores/uiStore";
import type { DashboardCardKey } from "@/stores/uiStore";

/**
 * Returns ``[isDisabled, setDisabled]`` for the named dashboard
 * card. ``setDisabled(true)`` moves the card to the disabled
 * rail; ``setDisabled(false)`` brings it back into the active
 * grid (appended to the end of the order).
 */
export function useDashboardCardDisabled(
  cardKey: DashboardCardKey,
): [boolean, (next: boolean) => void] {
  const isDisabled = useUiStore((s) =>
    s.dashboardDisabled.includes(cardKey),
  );
  const disable = useUiStore((s) => s.disableDashboardCard);
  const enable = useUiStore((s) => s.enableDashboardCard);
  const setDisabled = (next: boolean) => {
    if (next) disable(cardKey);
    else enable(cardKey);
  };
  return [isDisabled, setDisabled];
}
