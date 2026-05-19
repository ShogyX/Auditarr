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

import type { ResolutionBucket } from "@/hooks/useMedia";

import { AdvancedFilterMenu } from "./AdvancedFilterMenu";
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
  /** v1.9 Stage 2.4 — optional id→filename map for the delete
   *  confirmation dialog. The page passes the currently-rendered
   *  rows; the toolbar threads it through to the selection bar.
   *  Optional so callers that don't have names on hand still work
   *  (the dialog falls back to a placeholder). */
  selectedNames?: Map<string, string>;
  onClearSelection: () => void;
  /** Stage 02 — per-column filter row toggle. When true, the
   *  ``<FilesTable>`` renders a filter input beneath each
   *  sortable column header. Defaults off so the toolbar shows
   *  the same on a fresh install. */
  showColumnFilters?: boolean;
  onToggleColumnFilters?: () => void;
  /** v1.10 — Advanced filter axes (tags + rules include/exclude,
   *  subtitles tri-state, resolution bucket). All optional so test
   *  mounts that don't care about the menu still type-check. */
  tagsInclude?: Set<string>;
  tagsExclude?: Set<string>;
  tagsIncludeAll?: boolean;
  onToggleTagInclude?: (tag: string) => void;
  onToggleTagExclude?: (tag: string) => void;
  onTagsIncludeAll?: (v: boolean) => void;
  rulesInclude?: Set<string>;
  rulesExclude?: Set<string>;
  rulesIncludeAll?: boolean;
  onToggleRuleInclude?: (ruleId: string) => void;
  onToggleRuleExclude?: (ruleId: string) => void;
  onRulesIncludeAll?: (v: boolean) => void;
  hasSubtitles?: boolean | undefined;
  onHasSubtitles?: (v: boolean | undefined) => void;
  resolutionBucket?: ResolutionBucket | "";
  onResolutionBucket?: (v: ResolutionBucket | "") => void;
  onClearAdvanced?: () => void;
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

      {/* v1.10 — Advanced filter popover lives next to the codec
          menu so all the "narrow the view" affordances cluster
          together visually. Only renders when the page passes
          the handlers, so test mounts without these still work. */}
      {props.onToggleTagInclude &&
      props.onToggleTagExclude &&
      props.onToggleRuleInclude &&
      props.onToggleRuleExclude &&
      props.onHasSubtitles &&
      props.onResolutionBucket &&
      props.onClearAdvanced &&
      props.onTagsIncludeAll &&
      props.onRulesIncludeAll ? (
        <AdvancedFilterMenu
          tagsInclude={props.tagsInclude ?? new Set()}
          tagsExclude={props.tagsExclude ?? new Set()}
          tagsIncludeAll={!!props.tagsIncludeAll}
          onToggleTagInclude={props.onToggleTagInclude}
          onToggleTagExclude={props.onToggleTagExclude}
          onTagsIncludeAll={props.onTagsIncludeAll}
          rulesInclude={props.rulesInclude ?? new Set()}
          rulesExclude={props.rulesExclude ?? new Set()}
          rulesIncludeAll={!!props.rulesIncludeAll}
          onToggleRuleInclude={props.onToggleRuleInclude}
          onToggleRuleExclude={props.onToggleRuleExclude}
          onRulesIncludeAll={props.onRulesIncludeAll}
          hasSubtitles={props.hasSubtitles}
          onHasSubtitles={props.onHasSubtitles}
          resolutionBucket={props.resolutionBucket ?? ""}
          onResolutionBucket={props.onResolutionBucket}
          onClearAll={props.onClearAdvanced}
        />
      ) : null}

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
          selectedNames={props.selectedNames}
          onClear={onClearSelection}
        />
      ) : null}
    </div>
  );
}
