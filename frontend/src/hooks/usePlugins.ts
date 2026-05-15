import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

export type PluginStatus = "loaded" | "errored" | "failed_to_load";

export interface PluginSummary {
  id: string;
  name: string;
  version: string;
  type: string;
  // Stage 25: enriched fields. ``description``, ``author``, and
  // ``has_settings`` come from the manifest; ``status`` and
  // ``last_error`` are loader-derived state.
  description?: string;
  author?: string;
  status?: PluginStatus;
  last_error?: string | null;
  has_settings?: boolean;
  routes?: boolean | string[];
  capabilities?: string[];
}

export interface PluginSettings {
  id: string;
  plugin_id: string;
  values: Record<string, unknown>;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PluginSettingsSchema {
  plugin_id: string;
  schema: Record<string, unknown> | null;
  defaults: Record<string, unknown> | null;
}

export interface GalleryPlugin {
  id: string;
  name: string;
  description: string | null;
  author: string | null;
  version: string | null;
  source_url: string | null;
  install_url: string | null;
  install_instructions: string | null;
  categories: string[];
  installed: boolean;
}

export interface GalleryFetch {
  ok: boolean;
  feed_url: string;
  plugins: GalleryPlugin[];
  detail: string | null;
}

// ── Hooks ─────────────────────────────────────────────────────
export function usePlugins() {
  return useQuery({
    queryKey: ["plugins", "list"],
    queryFn: () => apiClient.get<PluginSummary[]>("/plugins"),
    staleTime: 30_000,
  });
}

export function usePluginSchema(pluginId: string | null) {
  return useQuery({
    queryKey: ["plugins", "schema", pluginId],
    queryFn: () => apiClient.get<PluginSettingsSchema>(`/plugins/${pluginId}/settings/schema`),
    enabled: !!pluginId,
    staleTime: 60_000,
  });
}

export function usePluginSettings(pluginId: string | null) {
  return useQuery({
    queryKey: ["plugins", "settings", pluginId],
    queryFn: () => apiClient.get<PluginSettings | null>(`/plugins/${pluginId}/settings`),
    enabled: !!pluginId,
    staleTime: 30_000,
  });
}

export function usePutPluginSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      pluginId,
      values,
      notes,
    }: {
      pluginId: string;
      values: Record<string, unknown>;
      notes?: string | null;
    }) =>
      apiClient.put<PluginSettings>(`/plugins/${pluginId}/settings`, {
        values,
        notes: notes ?? null,
      }),
    onSuccess: () => invalidateRelated(qc, "plugin"),
  });
}

export function usePluginGallery() {
  return useQuery({
    queryKey: ["plugins", "gallery"],
    queryFn: () => apiClient.get<GalleryFetch>("/plugins/gallery"),
    staleTime: 5 * 60_000,
  });
}

// ── Stage 25: reload ───────────────────────────────────────────

/** Reload a single plugin from disk. Returns the new summary. */
export function useReloadPlugin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pluginId: string) =>
      apiClient.post<PluginSummary>(
        `/plugins/${encodeURIComponent(pluginId)}/reload`,
      ),
    onSuccess: () => {
      // Reload changes the plugin's status / last_error; the broad
      // plugin invalidation also refreshes anything plugin-registered
      // (integration kinds, rule kinds, notification kinds, etc.).
      invalidateRelated(qc, "plugin");
    },
  });
}

// ── Stage 32: install (upload) + uninstall ─────────────────────

/** Install a plugin from an uploaded zip. Returns the new summary
 *  so the UI can splice it into the list without a re-fetch (the
 *  list cache is still invalidated for any related views).
 *
 *  Errors flow through the normal ``ApiError`` path — 409 on id
 *  collision, 422 on bad zip / bad manifest / unsafe path / too
 *  large, 403 if non-admin, 401 if not logged in. The page
 *  surfaces the ``message`` directly to the operator. */
export function useInstallPlugin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiClient.postForm<PluginSummary>("/plugins/install", form);
    },
    onSuccess: () => invalidateRelated(qc, "plugin"),
  });
}

/** Uninstall a plugin: lifecycle teardown + delete files from
 *  disk. Returns ``{id, removed, warnings}`` so the caller can
 *  surface route-unmount-limitation warnings to the operator. */
export interface UninstallResult {
  id: string;
  removed: boolean;
  warnings: string[];
}
export function useUninstallPlugin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pluginId: string) =>
      apiClient.delete<UninstallResult>(
        `/plugins/${encodeURIComponent(pluginId)}`,
      ),
    onSuccess: () => invalidateRelated(qc, "plugin"),
  });
}
