/**
 * Stage 3 — Files toolbar.
 *
 * Extracted from the inline ``FilterToolbar`` in ``FilesPage.tsx``.
 * Preserves the exact CSS contract (``.files-toolbar``,
 * ``.files-toolbar-search``) and DOM shape so the existing
 * ``FilesPage.test.tsx`` selectors continue to match.
 *
 * Owns layout only — all state and handlers flow in from the parent so
 * the FilesPage hook (``useFilesPageState``) remains the single source
 * of truth for filter values and URL deep-link state.
 *
 * Composition note: the search input keeps its ``.settings-input`` class
 * deliberately. Migrating to the Stage 1 ``Input variant="search"``
 * primitive would change the rendered DOM and require updating the
 * stage 31 codec-filter test which selects on placeholder text. We
 * defer that swap to "Stage 3b: DataGrid adoption" once a visual-diff
 * baseline locks the current rendering.
 */

import { Icon } from "@/components/ui/Icon";
import { fmtNum } from "@/lib/format";
import {
  FILES_COLUMNS,
  type FilesColumnKey,
} from "@/stores/filesPrefsStore";

import { CATEGORY_OPTIONS } from "./filesShared";
import { CodecFilterMenu } from "./CodecFilterMenu";
import { ColumnVisibilityMenu } from "./ColumnVisibilityMenu";
import { FilesSelectionActions } from "./FilesSelectionActions";

export interface FilesToolbarProps {
  libraries: { id: string; name: string }[];
  libraryId: string;
  onLibrary: (v: string) => void;
  category: string;
  onCategory: (v: string) => void;
  // Stage 27's ``quarantineView`` + ``onQuarantineView`` props
  // lived here. Stage 05 (v1.7) retired the quarantine workflow
  // (Section A.0 — "delete means delete"); the toolbar no longer
  // exposes a quarantine select.
  search: string;
  onSearch: (v: string) => void;
  /** Stage 31: codec + container filter sets. The picker lives in its
   *  own popover (``CodecFilterMenu``); the toolbar just forwards the
   *  state and the per-checkbox handlers. */
  activeCodecs: Set<string>;
  activeContainers: Set<string>;
  onToggleCodec: (codec: string) => void;
  onToggleContainer: (container: string) => void;
  onClearCodecsAndContainers: () => void;
  visibleColumns: FilesColumnKey[];
  onToggleColumn: (key: FilesColumnKey) => void;
  onResetColumns: () => void;
  total: number;
  shown: number;
  selectionCount: number;
  selectedIds: Set<string>;
  onClearSelection: () => void;
  /** Stage 02 — per-column filter row toggle. When true, the
   *  ``<FilesTable>`` renders a filter input beneath each
   *  sortable column header. Defaults off so the toolbar shows
   *  the same on a fresh install. */
  showColumnFilters?: boolean;
  onToggleColumnFilters?: () => void;
}

export function FilesToolbar(props: FilesToolbarProps) {
  const {
    libraries,
    libraryId,
    onLibrary,
    category,
    onCategory,
    search,
    onSearch,
    activeCodecs,
    activeContainers,
    onToggleCodec,
    onToggleContainer,
    onClearCodecsAndContainers,
    visibleColumns,
    onToggleColumn,
    onResetColumns,
    total,
    shown,
    selectionCount,
    selectedIds,
    onClearSelection,
    showColumnFilters = false,
    onToggleColumnFilters,
  } = props;

  return (
    <div className="files-toolbar">
      <div className="files-toolbar-search">
        <Icon
          name="search"
          size={14}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
        />
        <input
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search path or filename…"
          className="settings-input pl-7"
          style={{ width: "100%" }}
        />
      </div>

      <select
        className="settings-input"
        value={libraryId}
        onChange={(e) => onLibrary(e.target.value)}
        aria-label="Library filter"
      >
        <option value="">All libraries</option>
        {libraries.map((lib) => (
          <option key={lib.id} value={lib.id}>
            {lib.name}
          </option>
        ))}
      </select>

      <select
        className="settings-input"
        value={category}
        onChange={(e) => onCategory(e.target.value)}
        aria-label="Category filter"
      >
        {CATEGORY_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      {/* Stage 27 rendered a tri-state quarantine view select here
          (hide/include/only). Stage 05 (v1.7) removed it — the
          quarantine workflow it served is gone. */}

      {/* Stage 31: codec + container picker. Lives between the
          built-in <select>s and the column-visibility menu — its
          popover sits in the same row visually but opens
          downward, matching the column menu. */}
      <CodecFilterMenu
        selectedCodecs={activeCodecs}
        selectedContainers={activeContainers}
        onToggleCodec={onToggleCodec}
        onToggleContainer={onToggleContainer}
        onClear={onClearCodecsAndContainers}
      />

      <div className="flex-1" />

      <span className="text-[12px] text-muted whitespace-nowrap">
        {fmtNum(shown)} of {fmtNum(total)} files
      </span>

      {/* Stage 02 — per-column filter toggle. Renders a small icon
          button between the count and the column-visibility menu.
          Off by default; pressing it surfaces the filter row in
          the table header. Kept optional so existing test mounts
          of FilesToolbar without these props still work. */}
      {onToggleColumnFilters ? (
        <button
          type="button"
          className="settings-input flex items-center gap-1 px-2"
          aria-pressed={showColumnFilters}
          aria-label={
            showColumnFilters
              ? "Hide per-column filters"
              : "Show per-column filters"
          }
          onClick={onToggleColumnFilters}
          title={
            showColumnFilters
              ? "Hide per-column filters"
              : "Show per-column filters"
          }
        >
          <Icon name="filter" size={14} />
          <span className="text-[11.5px]">Filter</span>
        </button>
      ) : null}

      <ColumnVisibilityMenu
        columns={FILES_COLUMNS}
        visible={visibleColumns}
        onToggle={(k) => onToggleColumn(k as FilesColumnKey)}
        onReset={onResetColumns}
      />

      {selectionCount > 0 ? (
        <FilesSelectionActions
          count={selectionCount}
          selectedIds={selectedIds}
          onClear={onClearSelection}
        />
      ) : null}
    </div>
  );
}
