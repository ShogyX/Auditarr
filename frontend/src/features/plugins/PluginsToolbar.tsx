/**
 * Stage 6 — Plugins toolbar.
 *
 * Extracted from the inline toolbar in ``PluginsPage.tsx``. Owns:
 *
 *   - segmented tab strip Installed / Gallery with count badges
 *   - search input (Installed tab only)
 *   - the hidden file <input type=file> + visible "Install plugin"
 *     button that programmatically clicks it
 *
 * The segmented strip stays as ``.segmented`` (Stage 1 ``Tabs``
 * primitive would change DOM and break three of the
 * ``PluginsPage.test.tsx`` cases that pin role="tab" + aria-selected
 * + count badges). The migration is queued as Stage 6b.
 */

import { useRef } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";

import type { PluginsTab } from "./pluginsShared";

export interface PluginsToolbarProps {
  tab: PluginsTab;
  onTab: (next: PluginsTab) => void;
  installedCount: number;
  galleryCount: number;
  search: string;
  onSearch: (next: string) => void;
  /** Triggered after the user picks a zip from the hidden file
   *  input. The parent handles the actual install mutation. */
  onInstallFile: (file: File) => void;
  installPending: boolean;
}

export function PluginsToolbar({
  tab,
  onTab,
  installedCount,
  galleryCount,
  search,
  onSearch,
  onInstallFile,
  installPending,
}: PluginsToolbarProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="rules-toolbar">
      <div className="segmented" role="tablist" aria-label="Plugins view">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "installed"}
          className={tab === "installed" ? "on" : ""}
          onClick={() => onTab("installed")}
        >
          Installed{" "}
          <span className="font-mono text-muted-2 ml-1">{installedCount}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "gallery"}
          className={tab === "gallery" ? "on" : ""}
          onClick={() => onTab("gallery")}
        >
          Gallery{" "}
          <span className="font-mono text-muted-2 ml-1">{galleryCount}</span>
        </button>
      </div>

      {tab === "installed" ? (
        <div className="rules-toolbar-search">
          <Icon
            name="search"
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search plugins…"
            className="settings-input pl-7"
            style={{ width: "100%" }}
          />
        </div>
      ) : null}

      <div className="flex-1" />

      {/* Stage 32: Install plugin trigger. Lives on the right side
          of the toolbar, consistent with page-level primary-CTA
          placement on Rules, Settings, etc. The hidden file input
          is the actual <input>; the visible button programmatically
          clicks it. Reset the input's value after each pick so
          re-selecting the same file fires a fresh change event. */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".zip,application/zip"
        className="hidden"
        aria-label="Plugin zip file"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          onInstallFile(file);
        }}
      />
      <Button
        size="sm"
        variant="primary"
        onClick={() => fileInputRef.current?.click()}
        disabled={installPending}
        title="Upload a plugin zip"
      >
        <Icon
          name={installPending ? "refresh" : "plus"}
          size={12}
          className={installPending ? "animate-spin" : undefined}
        />
        <span className="ml-1">
          {installPending ? "Installing…" : "Install plugin"}
        </span>
      </Button>
    </div>
  );
}
