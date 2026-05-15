/**
 * Stage 5 — Automation page (slim orchestrator).
 *
 * Stage 10 audit fix (Issue 15): operational body moved to
 * ``AutomationTabContent`` so the Rules page can render the same
 * surface as a tab. This component is preserved so the existing
 * /automation tests in test-pages.test.tsx and BugHunt1.test.tsx
 * keep mounting a real page rather than chasing a redirect; the
 * route in AppRoutes now redirects /automation → /rules?tab=automation,
 * so this wrapper isn't reached in production navigation but is
 * still a valid renderable.
 *
 * @deprecated Stage 2 (audit follow-up). The canonical surface is
 * ``/rules?tab=automation``. The ``/automation`` route still resolves
 * (it redirects via ``AppRoutes``) but this page itself is not part
 * of normal navigation. The export is kept intentionally so the
 * existing page-mount smoke tests don't have to be rewritten and so
 * any external bookmarks / scripts loading this module directly do
 * not 404. Do NOT add new features here — they belong in
 * ``AutomationTabContent``.
 */

import { PageHeader } from "@/components/shell/PageHeader";
import { useHelpKey } from "@/hooks/useHelpKey";

import { AutomationTabContent } from "./AutomationTabContent";

export function AutomationPage() {
  useHelpKey("automation.overview");

  return (
    <>
      <PageHeader
        title="Automation"
        sub="Schedules, run history, and the optimization queue"
        helpKey="automation.overview"
      />
      <div className="p-6 flex flex-col gap-6 max-w-5xl">
        <AutomationTabContent />
      </div>
    </>
  );
}
