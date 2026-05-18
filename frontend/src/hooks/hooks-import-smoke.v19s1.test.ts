/**
 * v1.9 Stage 1.3 — hook-modules import smoke.
 *
 * Catches:
 *   1. Top-level errors at module evaluation time (a missing import,
 *      a typo that breaks circular-dep resolution).
 *   2. Hooks accidentally removed from the public surface — the
 *      assertions below pin a baseline set of named exports we know
 *      every page consumes.
 *
 * This is the "audit catches the regression cheap" guard the v1.9
 * Stage 1.3 plan calls for: a single test that statically pulls
 * every hook module so a future PR can't silently break the
 * invalidation graph by accidentally dropping a hook from the
 * registry.
 */

import { describe, expect, it } from "vitest";

import * as useAuth from "@/hooks/useAuth";
import * as useAutomation from "@/hooks/useAutomation";
import * as useChangelog from "@/hooks/useChangelog";
import * as useDashboard from "@/hooks/useDashboard";
import * as useDocs from "@/hooks/useDocs";
import * as useHelpKey from "@/hooks/useHelpKey";
import * as useIntegrations from "@/hooks/useIntegrations";
import * as useMedia from "@/hooks/useMedia";
import * as useNotifications from "@/hooks/useNotifications";
import * as useOptimization from "@/hooks/useOptimization";
import * as usePlayback from "@/hooks/usePlayback";
import * as usePlugins from "@/hooks/usePlugins";
import * as useRules from "@/hooks/useRules";
import * as useRuntimeSettings from "@/hooks/useRuntimeSettings";
import * as useScanProgress from "@/hooks/useScanProgress";
import * as useSystem from "@/hooks/useSystem";
import * as useUpdater from "@/hooks/useUpdater";
import * as useWebSocketEvents from "@/hooks/useWebSocketEvents";

describe("v1.9 Stage 1.3 — hook modules import without error", () => {
  it("loads every hook module's namespace as a non-empty object", () => {
    const modules: Record<string, object> = {
      useAuth,
      useAutomation,
      useChangelog,
      useDashboard,
      useDocs,
      useHelpKey,
      useIntegrations,
      useMedia,
      useNotifications,
      useOptimization,
      usePlayback,
      usePlugins,
      useRules,
      useRuntimeSettings,
      useScanProgress,
      useSystem,
      useUpdater,
      useWebSocketEvents,
    };
    for (const [name, mod] of Object.entries(modules)) {
      // Each namespace must have at least one named export. ESM
      // import namespaces are objects with own enumerable keys for
      // each export; an empty namespace means the module is empty
      // or broken.
      const keys = Object.keys(mod);
      expect(
        keys.length,
        `module ${name} exports nothing — likely broken`,
      ).toBeGreaterThan(0);
    }
  });

  it("baseline hook exports are present", () => {
    // A small set of hooks every page in the app consumes. If any
    // of these are accidentally removed, several pages crash on
    // mount. Pinning them here surfaces the regression in CI
    // instead of in production.
    expect(typeof useAuth.useLogin).toBe("function");
    expect(typeof useAuth.useLogout).toBe("function");
    expect(typeof useMedia.useMediaList).toBe("function");
    expect(typeof useMedia.useTriggerScan).toBe("function");
    expect(typeof useRules.useRules).toBe("function");
    expect(typeof useScanProgress.useScanProgress).toBe("function");
    expect(typeof useScanProgress.useScanProgressWs).toBe("function");
    expect(typeof useUpdater.useUpdaterStatus).toBe("function");
    expect(typeof useUpdater.useRequestApply).toBe("function");
    // v1.9 Stage 1.2 — the new force-clear hook must be there.
    expect(typeof useUpdater.useForceClearApply).toBe("function");
  });
});
