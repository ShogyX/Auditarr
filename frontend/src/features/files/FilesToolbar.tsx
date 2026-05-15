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

import { CATEGORY_OPTIONS, type QuarantineView } from "./filesShared";
import { CodecFilterMenu } from "./CodecFilterMenu";
import { ColumnVisibilityMenu } from "./ColumnVisibilityMenu";
import { FilesSelectionActions } from "./FilesSelectionActions";

export interface FilesToolbarProps {
  libraries: { id: string; name: string }[];
  libraryId: string;
  onLibrary: (v: string) => void;
  category: string;
  onCategory: (v: string) => void;
  quarantineView: QuarantineView;
  onQuarantineView: (v: QuarantineView) => void;
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
}

export function FilesToolbar(props: FilesToolbarProps) {
  const {
    libraries,
    libraryId,
    onLibrary,
    category,
    onCategory,
    quarantineView,
    onQuarantineView,
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

      {/* Stage 27: quarantine view mode. Tri-state because operators
          want three different views: normal (hide), audit (only),
          and "show me everything" (include). A boolean toggle
          couldn't express the "only quarantined" case, which is the
          most operationally useful — it's the dedicated review
          surface where the operator decides what to release. */}
      <select
        className="settings-input"
        value={quarantineView}
        onChange={(e) => onQuarantineView(e.target.value as QuarantineView)}
        aria-label="Quarantine view mode"
      >
        <option value="hide">Hide quarantined</option>
        <option value="include">Include quarantined</option>
        <option value="only">Quarantined only</option>
      </select>

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
