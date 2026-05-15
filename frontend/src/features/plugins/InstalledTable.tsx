/**
 * Stage 6 — Installed plugins table.
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

import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { type usePlugins, type PluginSummary } from "@/hooks/usePlugins";

import { PluginRow } from "./PluginRow";

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
  if (visiblePlugins.length === 0) {
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
          {visiblePlugins.map((p) => (
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
