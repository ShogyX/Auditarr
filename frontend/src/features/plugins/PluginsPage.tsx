/**
 * Stage 6 — Plugins page (slim orchestrator).
 *
 * Composes:
 *   - ``PageHeader``                  — title / subtitle
 *   - ``PluginsToolbar``              — tabs + search + install
 *   - ``InstalledTable``              — installed plugins (filtered)
 *   - ``GalleryTable``                — remote gallery feed
 *   - ``LifecycleErrorsCard``         — errored / failed plugins panel
 *   - ``PluginSettingsDialog``        — schema-driven settings editor
 *   - ``UninstallConfirmDialog``      — destructive-action confirmation
 *
 * Pre-Stage-6:  801 LOC (plus 181 LOC settings dialog)
 * Post-Stage-6: ~140 LOC (this file)
 *
 * Stage 6 also adopts the Stage 1 ``Modal`` primitive inside the
 * settings dialog and the uninstall confirmation dialog. The
 * ``.dialog-*`` CSS family is retired here; the Auditarr UI now has
 * one canonical modal pattern (Modal/ModalHead/ModalBody/ModalFoot).
 */

import { useMemo, useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { useHelpKey } from "@/hooks/useHelpKey";
import {
  useInstallPlugin,
  usePluginGallery,
  usePlugins,
  useReloadPlugin,
  useUninstallPlugin,
  type PluginSummary,
} from "@/hooks/usePlugins";
import { toast } from "@/lib/toast";

import { GalleryTable } from "./GalleryTable";
import { InstalledTable } from "./InstalledTable";
import { LifecycleErrorsCard } from "./LifecycleErrorsCard";
import { PluginSettingsDialog } from "./PluginSettingsDialog";
import { PluginsToolbar } from "./PluginsToolbar";
import { UninstallConfirmDialog } from "./UninstallConfirmDialog";
import type { PluginsTab } from "./pluginsShared";

export function PluginsPage() {
  useHelpKey("plugins.overview");

  const plugins = usePlugins();
  const gallery = usePluginGallery();
  const reload = useReloadPlugin();
  // Stage 32: install (upload) + uninstall.
  const install = useInstallPlugin();
  const uninstall = useUninstallPlugin();
  const [tab, setTab] = useState<PluginsTab>("installed");
  const [configuring, setConfiguring] = useState<PluginSummary | null>(null);
  const [search, setSearch] = useState<string>("");
  // Stage 32: uninstall confirmation. We use a confirmation step
  // because uninstall is destructive (files removed from disk) and
  // the page-level toast feedback comes too late to back out of.
  const [uninstalling, setUninstalling] = useState<PluginSummary | null>(null);

  // Filter is client-side because the loader's list is small
  // (typically <20 entries); a backend search would be over-engineering.
  const visiblePlugins = useMemo(() => {
    const all = plugins.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (p) =>
        p.id.toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q) ||
        (p.author ?? "").toLowerCase().includes(q),
    );
  }, [plugins.data, search]);

  const erroredPlugins = useMemo(
    () =>
      (plugins.data ?? []).filter(
        (p) => p.status === "errored" || p.status === "failed_to_load",
      ),
    [plugins.data],
  );

  async function onReload(plugin: PluginSummary) {
    try {
      const result = await reload.mutateAsync(plugin.id);
      if (result.status === "loaded") {
        toast(`Reloaded ${plugin.name}`, "ok");
      } else if (result.status === "errored") {
        toast(
          `${plugin.name} reloaded but raised: ${result.last_error}`,
          "warn",
          5000,
        );
      } else {
        toast(
          `${plugin.name} failed to reload: ${result.last_error}`,
          "error",
          6000,
        );
      }
    } catch (err) {
      toast(
        `Could not reload ${plugin.name}: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  async function onInstallFile(file: File) {
    try {
      const result = await install.mutateAsync(file);
      const note =
        result.status === "loaded"
          ? "installed"
          : result.status === "errored"
            ? "installed with a lifecycle error"
            : "installed but failed to start";
      toast(
        `${result.name} ${note}`,
        result.status === "loaded" ? "ok" : "warn",
        5000,
      );
    } catch (err) {
      toast(
        `Install failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        7000,
      );
    }
  }

  async function onConfirmUninstall(plugin: PluginSummary) {
    try {
      const result = await uninstall.mutateAsync(plugin.id);
      if (result.warnings.length > 0) {
        // Joining the warnings into one toast keeps the UI calm — a
        // separate toast per warning would spam.
        toast(
          `Uninstalled ${plugin.name}. ${result.warnings.join(" ")}`,
          "warn",
          7000,
        );
      } else {
        toast(`Uninstalled ${plugin.name}`, "ok");
      }
      setUninstalling(null);
    } catch (err) {
      toast(
        `Could not uninstall ${plugin.name}: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  return (
    <>
      <PageHeader
        title="Plugins"
        sub="Discover, configure, and reload backend plugins without restarting the host"
        helpKey="plugins.overview"
      />
      <div className="p-6 flex flex-col gap-4 plugins-page">
        <Card>
          <PluginsToolbar
            tab={tab}
            onTab={setTab}
            installedCount={plugins.data?.length ?? 0}
            galleryCount={gallery.data?.plugins?.length ?? 0}
            search={search}
            onSearch={setSearch}
            onInstallFile={onInstallFile}
            installPending={install.isPending}
          />

          {tab === "installed" ? (
            <InstalledTable
              plugins={plugins}
              visiblePlugins={visiblePlugins}
              onConfigure={setConfiguring}
              onReload={onReload}
              reloadingId={reload.isPending ? reload.variables ?? null : null}
              onUninstall={setUninstalling}
              uninstallingId={
                uninstall.isPending ? (uninstall.variables ?? null) : null
              }
            />
          ) : (
            <GalleryTable gallery={gallery} />
          )}
        </Card>

        {erroredPlugins.length > 0 ? (
          <LifecycleErrorsCard plugins={erroredPlugins} />
        ) : null}
      </div>

      {configuring ? (
        <PluginSettingsDialog
          plugin={configuring}
          onClose={() => setConfiguring(null)}
        />
      ) : null}

      {/* Stage 32: uninstall confirmation. Destructive action +
          irreversible disk write → confirm before firing. */}
      {uninstalling ? (
        <UninstallConfirmDialog
          plugin={uninstalling}
          isPending={uninstall.isPending}
          onConfirm={() => onConfirmUninstall(uninstalling)}
          onClose={() => setUninstalling(null)}
        />
      ) : null}
    </>
  );
}
