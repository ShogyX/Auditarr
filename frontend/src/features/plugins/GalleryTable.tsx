/**
 * Stage 6 — Gallery plugins table.
 *
 * Extracted from the inline ``GalleryTable`` in ``PluginsPage.tsx``.
 * Browse-only table — no actions other than "view source". The
 * gallery feed is opt-in via ``AUDITARR_PLUGIN_GALLERY_URL`` so the
 * empty-state copy distinguishes between "gallery disabled" and
 * "gallery returned no entries".
 */

import { Pill, Tag } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { usePluginGallery } from "@/hooks/usePlugins";

export interface GalleryTableProps {
  gallery: ReturnType<typeof usePluginGallery>;
}

export function GalleryTable({ gallery }: GalleryTableProps) {
  if (gallery.isLoading) {
    return (
      <div className="px-4 py-12">
        <LoadingState label="Loading gallery…" />
      </div>
    );
  }
  if (gallery.isError) {
    return (
      <div className="px-4 py-12">
        <ErrorState
          title="Failed to fetch gallery"
          description={(gallery.error as Error)?.message}
        />
      </div>
    );
  }
  if (!gallery.data?.ok) {
    return (
      <div className="px-4 py-12">
        <EmptyState
          icon="folder"
          title="Gallery unavailable"
          description={
            gallery.data?.detail ??
            "Set AUDITARR_PLUGIN_GALLERY_URL in your environment to enable the gallery."
          }
        />
      </div>
    );
  }
  if ((gallery.data.plugins?.length ?? 0) === 0) {
    return (
      <div className="px-4 py-12">
        <EmptyState
          icon="folder"
          title="No gallery plugins"
          description="The configured gallery feed returned no entries."
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
            <th>Categories</th>
            <th>Version</th>
            <th>Description</th>
            <th aria-label="Row actions" />
          </tr>
        </thead>
        <tbody>
          {gallery.data.plugins.map((entry) => (
            <tr key={entry.id} className="files-table-row">
              <td>
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="plugin-monogram" aria-hidden="true">
                    {entry.name.slice(0, 2).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium truncate">
                      {entry.name}
                      {entry.author ? (
                        <span className="text-[11px] text-muted-2 ml-1.5">
                          by {entry.author}
                        </span>
                      ) : null}
                    </div>
                    <div className="text-[11.5px] text-muted-2 font-mono truncate">
                      {entry.id}
                    </div>
                  </div>
                </div>
              </td>
              <td>
                <div className="flex flex-wrap gap-1">
                  {entry.categories.length > 0 ? (
                    entry.categories.map((c) => <Tag key={c}>{c}</Tag>)
                  ) : (
                    <span className="text-muted-2">—</span>
                  )}
                </div>
              </td>
              <td className="font-mono text-[12px]">{entry.version ?? "—"}</td>
              <td>
                <div className="text-[12.5px] text-text-2 truncate max-w-[320px]">
                  {entry.description ?? "—"}
                </div>
              </td>
              <td className="rules-row-actions">
                {entry.installed ? (
                  <Pill sev="ok">installed</Pill>
                ) : entry.source_url ? (
                  <a
                    href={entry.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11.5px] text-muted-2 hover:text-text underline"
                  >
                    Source ↗
                  </a>
                ) : (
                  <span className="text-muted-2 text-[11.5px]">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
