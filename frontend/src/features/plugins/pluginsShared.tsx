/**
 * Stage 6 — Plugins shared helpers.
 *
 * Single source of truth for the small bits previously inlined in
 * ``PluginsPage.tsx``: the status pill and the type alias.
 */

import { Pill } from "@/components/ui/Pill";
import type { PluginStatus } from "@/hooks/usePlugins";

export type PluginsTab = "installed" | "gallery";

/**
 * Status pill for a plugin row. The status enum is
 * ``loaded`` / ``errored`` / ``failed_to_load``; everything else
 * falls through to a neutral pill so a forward-compatible enum from
 * a backend rev doesn't crash the page.
 */
export function StatusPill({ status }: { status: PluginStatus }) {
  switch (status) {
    case "loaded":
      return <Pill sev="ok">loaded</Pill>;
    case "errored":
      return <Pill sev="warn">errored</Pill>;
    case "failed_to_load":
      return <Pill sev="error">failed</Pill>;
    default:
      return <Pill>{status}</Pill>;
  }
}
