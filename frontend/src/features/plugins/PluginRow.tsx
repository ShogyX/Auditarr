/**
 * Stage 6 — Plugin row (installed-table row).
 *
 * Extracted from the inline ``PluginRow`` in ``PluginsPage.tsx``.
 * Preserves the exact DOM contract: ``tr.files-table-row``,
 * ``.plugin-monogram``, ``.rules-row-actions``, the Configure button
 * gated on ``has_settings``, Reload + Uninstall with their pending
 * disabled state and spinner.
 *
 * The Stage-1 ``DataGrid`` primitive is NOT adopted here because the
 * existing 9 + 8 = 17 Plugins tests pin the table's DOM structure.
 * Migration to DataGrid is queued as Stage 6b after a Playwright
 * visual baseline.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Tag } from "@/components/ui/Pill";
import type { PluginSummary } from "@/hooks/usePlugins";

import { StatusPill } from "./pluginsShared";

export interface PluginRowProps {
  plugin: PluginSummary;
  onConfigure: () => void;
  onReload: () => void;
  isReloading: boolean;
  onUninstall: () => void;
  isUninstalling: boolean;
}

export function PluginRow({
  plugin,
  onConfigure,
  onReload,
  isReloading,
  onUninstall,
  isUninstalling,
}: PluginRowProps) {
  return (
    <tr className="files-table-row">
      <td>
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="plugin-monogram" aria-hidden="true">
            {plugin.name.slice(0, 2).toUpperCase()}
          </div>
          <div className="min-w-0">
            <div className="text-[13px] font-medium truncate">
              {plugin.name}
              {plugin.author ? (
                <span className="text-[11px] text-muted-2 ml-1.5">
                  by {plugin.author}
                </span>
              ) : null}
            </div>
            <div className="text-[11.5px] text-muted-2 font-mono truncate">
              {plugin.id}
              {plugin.description ? ` · ${plugin.description}` : ""}
            </div>
          </div>
        </div>
      </td>
      <td>
        <Tag>{plugin.type}</Tag>
      </td>
      <td className="font-mono text-[12px]">{plugin.version}</td>
      <td>
        <StatusPill status={plugin.status ?? "loaded"} />
      </td>
      <td>
        <div className="flex flex-wrap gap-1">
          {(plugin.capabilities ?? []).length > 0 ? (
            (plugin.capabilities ?? []).map((c) => <Tag key={c}>{c}</Tag>)
          ) : (
            <span className="text-muted-2">—</span>
          )}
        </div>
      </td>
      <td className="rules-row-actions">
        {plugin.has_settings ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onConfigure}
            title="Configure plugin settings"
          >
            <Icon name="cog" size={12} /> Configure
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          onClick={onReload}
          disabled={isReloading}
          title="Reload this plugin from disk"
        >
          <Icon
            name="refresh"
            size={12}
            className={isReloading ? "animate-spin" : undefined}
          />
          <span className="ml-1">
            {isReloading ? "Reloading…" : "Reload"}
          </span>
        </Button>
        {/* Stage 32: per-row uninstall affordance. Opens the
            confirmation modal in the parent — never fires the
            mutation directly. ``last`` placement keeps the
            destructive action away from the more commonly-used
            Configure/Reload buttons. */}
        <Button
          size="sm"
          variant="ghost"
          onClick={onUninstall}
          disabled={isUninstalling}
          title="Uninstall this plugin (removes files from disk)"
        >
          <Icon name="trash" size={12} />
          <span className="ml-1">
            {isUninstalling ? "Uninstalling…" : "Uninstall"}
          </span>
        </Button>
      </td>
    </tr>
  );
}
