/**
 * Files page preferences (Stage 23).
 *
 * Persists per-user UI state that's specific to the Files page:
 * column visibility, page size, sort. The UI store
 * (``stores/uiStore.ts``) is reserved for global preferences
 * (theme, accent, navigation layout); page-local prefs live here so
 * the global namespace stays small and uncluttered.
 *
 * Persistence is opt-in per-key — if a user clears localStorage or
 * a new column is added later, the defaults kick in cleanly. The
 * ``always`` set in ``FILES_COLUMNS`` enforces that path stays
 * visible even if a stale persisted state forgets it.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { MediaSortKey } from "@/hooks/useMedia";

/** Every column the Files table CAN show, in display order. */
export const FILES_COLUMNS = [
  { key: "filename", label: "File", always: true },
  { key: "category", label: "Category", sortKey: "category" as const },
  { key: "severity", label: "Severity", sortKey: "severity" as const },
  { key: "size", label: "Size", sortKey: "size_bytes" as const, num: true },
  // Stage 3 (audit follow-up): codec now sorts via the new
  // backend-whitelisted ``video_codec`` key. Display label stays
  // "Codec" — the row cell still falls back to the audio codec
  // when the file has no video stream.
  { key: "codec", label: "Codec", sortKey: "video_codec" as const },
  // Stage 3 (audit follow-up): new Container column. The label is
  // brief because the toolbar already exposes a Codec/container
  // filter under that name; the column header is the
  // sort-affordance for the container probe field.
  { key: "container", label: "Container", sortKey: "container" as const },
  { key: "resolution", label: "Resolution" },
  { key: "subs", label: "Subs" },
  { key: "updated", label: "Updated", sortKey: "mtime" as const },
  { key: "extension", label: "Ext", sortKey: "extension" as const },
  // Stage 3 (audit follow-up): new optional column.
  //
  // Renders the list of rule names that contributed to the row's
  // severity, capped to three chips plus a "+N" overflow indicator.
  // Off by default — turning it on costs an extra join on the
  // server, and most operators only look at it when triaging.
  //
  // No ``sortKey`` because sorting by an aggregated list isn't a
  // well-defined operation; the closest equivalent is sorting by
  // severity (which the severity column already does).
  { key: "matched_rules", label: "Rules" },
  // Stage 13 (audit follow-up): optional tags column. Off by
  // default — turning it on costs the LEFT JOIN onto media_tags
  // server-side. Renders the first three tag NAMES as small chips
  // with a "+N" overflow indicator. Source distinction is hidden in
  // this column to keep the row compact; the drawer surfaces the
  // grouped-by-source view.
  //
  // No ``sortKey`` because aggregated-list sorting isn't a well-
  // defined operation; operators sort by severity or filename when
  // triaging by tag and then scan visually.
  { key: "tags", label: "Tags" },
] as const;

export type FilesColumnKey = (typeof FILES_COLUMNS)[number]["key"];

const DEFAULT_VISIBLE: FilesColumnKey[] = [
  "filename",
  "category",
  "severity",
  "size",
  "codec",
  "resolution",
  "subs",
];

const ALL_KEYS = FILES_COLUMNS.map((c) => c.key as FilesColumnKey);
const ALWAYS_KEYS = FILES_COLUMNS.filter(
  (c) => "always" in c && c.always,
).map((c) => c.key as FilesColumnKey);

export type SortState = {
  key: MediaSortKey;
  dir: "asc" | "desc";
};

interface FilesPrefsState {
  visibleColumns: FilesColumnKey[];
  pageSize: number;
  sort: SortState;
  setVisibleColumns: (cols: FilesColumnKey[]) => void;
  toggleColumn: (key: FilesColumnKey) => void;
  resetColumns: () => void;
  setPageSize: (n: number) => void;
  setSort: (sort: SortState) => void;
}

const DEFAULTS = {
  visibleColumns: DEFAULT_VISIBLE,
  pageSize: 50,
  // Stage 3 (audit follow-up): the canonical sort key for the
  // severity column is now ``severity``. The backend's
  // SORTABLE_COLUMNS whitelist accepts both ``severity`` and the
  // legacy ``severity_rank``, but the column header sends
  // ``severity`` from now on. Persisted ``severity_rank`` from
  // older clients still works (it sorts identically).
  sort: { key: "severity" as MediaSortKey, dir: "desc" as const },
};

export const useFilesPrefs = create<FilesPrefsState>()(
  persist(
    (set, get) => ({
      ...DEFAULTS,
      setVisibleColumns: (cols) => {
        // Force ``always`` columns to stay visible — a stale persisted
        // state must not let us hide the path column. We also drop any
        // unknown keys (column removed in a later release) so the
        // table never tries to render a phantom column.
        const next = Array.from(
          new Set([...cols.filter((k) => ALL_KEYS.includes(k)), ...ALWAYS_KEYS]),
        );
        set({ visibleColumns: next });
      },
      toggleColumn: (key) => {
        if (ALWAYS_KEYS.includes(key)) return; // can't hide path
        const cur = new Set(get().visibleColumns);
        if (cur.has(key)) cur.delete(key);
        else cur.add(key);
        get().setVisibleColumns(Array.from(cur));
      },
      resetColumns: () => set({ visibleColumns: DEFAULT_VISIBLE }),
      setPageSize: (n) => {
        // Cap matches the backend's ``limit`` ceiling so we can't ask
        // for more rows than the API will return.
        const clamped = Math.max(10, Math.min(500, Math.floor(n)));
        set({ pageSize: clamped });
      },
      setSort: (sort) => set({ sort }),
    }),
    {
      name: "auditarr.files.prefs",
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        // Belt-and-suspenders: re-apply the always-visible constraint
        // on rehydrate too. If the persisted state somehow predates
        // an always-column being added, this gracefully repairs it.
        const visible = Array.from(
          new Set([
            ...state.visibleColumns.filter((k) =>
              ALL_KEYS.includes(k),
            ),
            ...ALWAYS_KEYS,
          ]),
        );
        if (
          visible.length !== state.visibleColumns.length ||
          visible.some((k, i) => k !== state.visibleColumns[i])
        ) {
          state.visibleColumns = visible;
        }
      },
    },
  ),
);
