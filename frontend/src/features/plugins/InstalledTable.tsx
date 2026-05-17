/**
 * Stage 6 — Installed plugins table.
 *
 * Stage 04 (v1.7) — built-in connectors (plex / jellyfin /
 * sonarr / radarr / bazarr / tdarr) are hidden from this list by
 * default. They're managed under the Integrations page; surfacing
 * them on the Plugins page implied operators could uninstall
 * them, which they can't. A small toggle ("Show built-in
 * connectors") restores them for the rare debugging case. The
 * ``hello`` / example plugin stays visible because it's literally
 * the canonical example of how to author a plugin.
 *
 * Extracted from the inline ``InstalledTable`` in ``PluginsPage.tsx``.
 * Four-way state branch (loading / error / empty-overall /
 * empty-after-search) followed by the rendered table of
 * ``PluginRow`` instances.
 *
 * Preserved DOM exactly so the 9 ``PluginsPage.test.tsx`` cases
 * continue to pass: ``files-table-wrap``, ``files-table role="grid"``,
 * the column headers verbatim (Plugin / Type / Version / Status /
 * Capabilities + invisible row-actions column).
 */

import { useState } from "react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { type usePlugins, type PluginSummary } from "@/hooks/usePlugins";

import { PluginRow } from "./PluginRow";

/**
 * Built-in connector plugin IDs. Hidden from the Plugins page by
 * default — these are first-party integrations whose lifecycle is
 * managed under the Integrations page, not the plugin store.
 *
 * Stage 10 added ``virustotal``. With VT now a first-class
 * integration that lives on the Integrations page, its plugin
 * surface must be hidden here so it doesn't double up under
 * Plugins. The plugin module is still discovered/registered
 * by the loader — this exclusion only affects rendering on
 * the operator-facing Plugins page.
 */
export const BUILTIN_PLUGIN_IDS: ReadonlySet<string> = new Set([
  "plex",
  "jellyfin",
  "sonarr",
  "radarr",
  "bazarr",
  "tdarr",
  "virustotal",
]);

export interface InstalledTableProps {
  plugins: ReturnType<typeof usePlugins>;
  visiblePlugins: PluginSummary[];
  onConfigure: (plugin: PluginSummary) => void;
  onReload: (plugin: PluginSummary) => void;
  reloadingId: string | null;
  onUninstall: (plugin: PluginSummary) => void;
  uninstallingId: string | null;
}

export function InstalledTable({
  plugins,
  visiblePlugins,
  onConfigure,
  onReload,
  reloadingId,
  onUninstall,
  uninstallingId,
}: InstalledTableProps) {
  // Stage 04 — toggle for surfacing the hidden built-in connectors.
  // Transient (not persisted) — operators only flip this for
  // debugging; the default state on every fresh page load is
  // "hide built-ins".
  const [showBuiltins, setShowBuiltins] = useState<boolean>(false);

  if (plugins.isLoading) {
    return (
      <div className="px-4 py-12">
        <LoadingState label="Loading plugins…" />
      </div>
    );
  }
  if (plugins.isError) {
    return (
      <div className="px-4 py-12">
        <ErrorState
          title="Failed to load plugins"
          description={(plugins.error as Error)?.message}
        />
      </div>
    );
  }
  if ((plugins.data?.length ?? 0) === 0) {
    return (
      <div className="px-4 py-12">
        <EmptyState
          icon="folder"
          title="No plugins installed"
          description="Use the Install plugin button above to upload a plugin zip, or drop a plugin directory into the configured plugin folder and restart Auditarr."
        />
      </div>
    );
  }

  // Stage 04 — apply the built-in filter on top of the
  // ``visiblePlugins`` (which already reflects the toolbar search).
  // The page-level filter and the built-in filter compose with AND
  // semantics: an operator searching for "plex" with built-ins
  // hidden sees nothing — clicking the toggle reveals the match.
  const builtinCount = visiblePlugins.filter((p) =>
    BUILTIN_PLUGIN_IDS.has(p.id),
  ).length;
  const filteredPlugins = showBuiltins
    ? visiblePlugins
    : visiblePlugins.filter((p) => !BUILTIN_PLUGIN_IDS.has(p.id));

  if (filteredPlugins.length === 0) {
    // Two empty cases: search yields nothing, OR everything in the
    // result is a built-in and the toggle is off. Distinguish them
    // so the operator knows whether to clear the search or flip the
    // toggle.
    if (builtinCount > 0 && !showBuiltins) {
      return (
        <div className="px-4 py-12 flex flex-col items-center gap-2">
          <EmptyState
            icon="folder"
            title="Only built-in connectors match"
            description={
              `${builtinCount} built-in connector(s) match your search but ` +
              "are hidden by default. Manage built-ins under the Integrations page."
            }
          />
          <button
            type="button"
            className="settings-input"
            onClick={() => setShowBuiltins(true)}
          >
            Show built-in connectors
          </button>
        </div>
      );
    }
    return (
      <div className="px-4 py-12">
        <EmptyState
          icon="folder"
          title="No plugins match"
          description="Clear the search to see every plugin."
        />
      </div>
    );
  }

  return (
    <div className="files-table-wrap">
      {/* Stage 04 — built-in connector toggle. Renders inline above
          the table; off by default so the page reads as plugin-only. */}
      <div className="px-4 py-2 flex items-center justify-end gap-2 text-[11.5px] text-muted-2">
        {builtinCount > 0 ? (
          <button
            type="button"
            className="settings-input"
            aria-pressed={showBuiltins}
            onClick={() => setShowBuiltins((v) => !v)}
            title={
              showBuiltins
                ? "Hide built-in connectors"
                : "Show built-in connectors"
            }
          >
            {showBuiltins
              ? `Hide ${builtinCount} built-in connector${builtinCount === 1 ? "" : "s"}`
              : `Show ${builtinCount} built-in connector${builtinCount === 1 ? "" : "s"}`}
          </button>
        ) : null}
      </div>
      <table className="files-table" role="grid">
        <thead>
          <tr>
            <th>Plugin</th>
            <th>Type</th>
            <th>Version</th>
            <th>Status</th>
            <th>Capabilities</th>
            <th aria-label="Row actions" />
          </tr>
        </thead>
        <tbody>
          {filteredPlugins.map((p) => (
            <PluginRow
              key={p.id}
              plugin={p}
              onConfigure={() => onConfigure(p)}
              onReload={() => onReload(p)}
              isReloading={reloadingId === p.id}
              onUninstall={() => onUninstall(p)}
              isUninstalling={uninstallingId === p.id}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
