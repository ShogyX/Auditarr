/**
 * Stage 3 — Files page state hook.
 *
 * Owns every piece of FilesPage state that used to live as inline
 * ``useState`` / ``useMemo`` / ``useEffect`` declarations in
 * ``FilesPage.tsx``:
 *
 *   - filter values (library, category, search, scope, active
 *     severities, active codecs, active containers)
 *   - pagination (current page)
 *   - row selection
 *   - drawer file
 *   - URL deep-link initialisation
 *   - filter → React-Query params memoisation
 *   - selection-reset side effect when filters change
 *
 * Stage 05 (v1.7) removed the quarantine view state that lived
 * here pre-stage; the Files page no longer exposes a quarantine
 * select.
 *
 * Behaviour is otherwise preserved so the four FilesPage test files
 * continue to pass without modification. The hook returns one big
 * object because the page itself is the only consumer; splitting into
 * sub-hooks would just relocate the boilerplate.
 */

import { useEffect, useMemo, useState } from "react";

import {
  useLibraries,
  useMediaList,
  useResetLibraryScans,
  useTriggerScan,
  useTriggerScanAll,
  type MediaFileSummary,
  type MediaFilters,
  type MediaSortKey,
} from "@/hooks/useMedia";
import { useScanProgress } from "@/hooks/useScanProgress";
import { apiClient } from "@/services/apiClient";
import { useFilesPrefs } from "@/stores/filesPrefsStore";

import {
  SEVERITY_KEYS,
  type ScopeMode,
  type SeverityKey,
} from "./filesShared";

export interface UseFilesPageState {
  /** Library list query (drives the dropdown). */
  libraries: ReturnType<typeof useLibraries>;
  /** Trigger-scan mutation. */
  triggerScan: ReturnType<typeof useTriggerScan>;
  /**
   * Stage 8 (audit follow-up): scan-all mutation. Enqueues a scan
   * for every enabled library via POST /scans/all.
   */
  triggerScanAll: ReturnType<typeof useTriggerScanAll>;
  /**
   * v1.8.1: reset stuck scans for a library. Surfaced via the
   * FilesPage error banner when a scan trigger returns 409.
   */
  resetLibraryScans: ReturnType<typeof useResetLibraryScans>;
  /** Live scan progress (websocket-backed). */
  scanProgress: ReturnType<typeof useScanProgress>;

  /* ── persisted prefs ───────────────────────────────────── */
  pageSize: number;
  sort: { key: MediaSortKey; dir: "asc" | "desc" };
  visibleColumns: ReturnType<typeof useFilesPrefs.getState>["visibleColumns"];
  toggleColumn: ReturnType<typeof useFilesPrefs.getState>["toggleColumn"];
  resetColumns: ReturnType<typeof useFilesPrefs.getState>["resetColumns"];
  /* Stage 02 — column widths + per-column filters. */
  columnWidths: ReturnType<typeof useFilesPrefs.getState>["columnWidths"];
  setColumnWidth: ReturnType<typeof useFilesPrefs.getState>["setColumnWidth"];
  perColumnFilters: ReturnType<
    typeof useFilesPrefs.getState
  >["perColumnFilters"];
  setPerColumnFilter: ReturnType<
    typeof useFilesPrefs.getState
  >["setPerColumnFilter"];
  /* Stage 02 — visibility of the per-column filter row. Transient
   * (not persisted) so the row collapses on reload — operators
   * who want it back hit the toolbar toggle. */
  showColumnFilters: boolean;
  setShowColumnFilters: (v: boolean) => void;

  /* ── transient page state ──────────────────────────────── */
  libraryId: string;
  setLibraryId: (id: string) => void;
  category: string;
  setCategory: (c: string) => void;
  search: string;
  setSearch: (s: string) => void;
  scope: ScopeMode;
  setScope: (s: ScopeMode) => void;
  // Stage 27's quarantineView state and setter were removed in
  // Stage 05 (v1.7) along with the quarantine workflow they served.
  activeSevs: Set<string>;
  toggleSev: (key: SeverityKey) => void;
  allSevs: () => void;
  noSevs: () => void;
  activeCodecs: Set<string>;
  toggleCodec: (codec: string) => void;
  activeContainers: Set<string>;
  toggleContainer: (container: string) => void;
  clearCodecsAndContainers: () => void;

  /* ── pagination + selection + drawer ───────────────────── */
  page: number;
  setPage: (p: number) => void;
  selected: Set<string>;
  toggleSel: (id: string) => void;
  clearSelection: () => void;
  toggleAllVisible: () => void;
  allVisibleSelected: boolean;
  someVisibleSelected: boolean;
  drawerFile: MediaFileSummary | null;
  setDrawerFile: (f: MediaFileSummary | null) => void;

  /* ── derived data ──────────────────────────────────────── */
  list: ReturnType<typeof useMediaList>;
  totalPages: number;
  clickSort: (key: MediaSortKey) => void;
}

export function useFilesPageState(): UseFilesPageState {
  const libraries = useLibraries();
  const triggerScan = useTriggerScan();
  const triggerScanAll = useTriggerScanAll();
  const resetLibraryScans = useResetLibraryScans();
  const scanProgress = useScanProgress();

  const pageSize = useFilesPrefs((s) => s.pageSize);
  const sort = useFilesPrefs((s) => s.sort);
  const setSort = useFilesPrefs((s) => s.setSort);
  const visibleColumns = useFilesPrefs((s) => s.visibleColumns);
  const toggleColumn = useFilesPrefs((s) => s.toggleColumn);
  const resetColumns = useFilesPrefs((s) => s.resetColumns);
  // Stage 02 — column resize + per-column filter prefs.
  const columnWidths = useFilesPrefs((s) => s.columnWidths);
  const setColumnWidth = useFilesPrefs((s) => s.setColumnWidth);
  const perColumnFilters = useFilesPrefs((s) => s.perColumnFilters);
  const setPerColumnFilter = useFilesPrefs((s) => s.setPerColumnFilter);
  const [showColumnFilters, setShowColumnFilters] = useState<boolean>(false);

  const [libraryId, setLibraryId] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [scope, setScope] = useState<ScopeMode>("all");
  // Stage 27 carried a ``quarantineView`` state field here. Stage
  // 05 (v1.7) retired it (Section A.0 — "delete means delete");
  // the page no longer offers a quarantine view toggle.
  const [activeSevs, setActiveSevs] = useState<Set<string>>(
    () => new Set(SEVERITY_KEYS),
  );
  // Stage 31: codec + container filter sets. Stored as ``Set<string>``
  // to match the severity filter shape; the ``filters`` memo
  // sort-and-join them so the React Query cache key stays stable
  // across renders. Empty set means "no filter" (server returns
  // all rows for that column).
  const [activeCodecs, setActiveCodecs] = useState<Set<string>>(
    () => new Set(),
  );
  const [activeContainers, setActiveContainers] = useState<Set<string>>(
    () => new Set(),
  );

  // Stage 14.1: dashboard deep-link via ``?severity=warn``.
  // Stage 26: also honor ``?library_id=<id>`` so the dashboard's
  // library cards can drill down into a filtered Files view.
  // Stage 31: deep-link from the dashboard's Categories card
  // via ``?video_codec=hevc`` / ``?container=matroska``.
  // Stage 02 (v1.7): ``?severity=`` accepts a comma-separated set
  // (``?severity=warn,high,error,crit``) so the dashboard's
  // Open-issues card can pre-filter to "everything actionable" in
  // one click, not just one severity at a time.
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const sev = sp.get("severity");
    if (sev) {
      const parts = sev
        .split(",")
        .map((s) => s.trim())
        .filter((s) => (SEVERITY_KEYS as readonly string[]).includes(s));
      if (parts.length > 0) {
        setActiveSevs(new Set(parts));
      }
    }
    const lib = sp.get("library_id");
    if (lib) {
      setLibraryId(lib);
    }
    const codec = sp.get("video_codec");
    if (codec) {
      setActiveCodecs(
        new Set(codec.split(",").map((c) => c.trim()).filter(Boolean)),
      );
    }
    const container = sp.get("container");
    if (container) {
      setActiveContainers(
        new Set(container.split(",").map((c) => c.trim()).filter(Boolean)),
      );
    }
  }, []);

  const [page, setPage] = useState<number>(0);

  // Selection lives in page state because it's transient — a refresh,
  // a filter change, or a column toggle should NOT preserve a stale
  // selection. We explicitly clear it on filter / page changes below;
  // without that the operator could end up applying a bulk action to
  // files they can no longer see, which is the worst failure mode
  // for this UX.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drawerFile, setDrawerFile] = useState<MediaFileSummary | null>(null);

  // Stage 14b (audit follow-up): ``?file_id=<id>`` deep-link.
  // When the URL carries a file id (e.g. cross-link from the
  // Rule editor's Matched files tab), fetch that file's summary
  // and open the drawer for it. Single-shot: runs once on mount,
  // then strips the param from the URL so a subsequent navigation
  // away and back doesn't repop the drawer.
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const fileId = sp.get("file_id");
    if (!fileId) return;
    let cancelled = false;
    apiClient
      .get<MediaFileSummary>(`/media/${encodeURIComponent(fileId)}`)
      .then((file) => {
        if (cancelled) return;
        setDrawerFile(file);
      })
      .catch(() => {
        // Silently ignore — the file may have been evicted between
        // when the link was generated and when it was followed. No
        // drawer pops; the operator stays on the unfiltered Files
        // page.
      })
      .finally(() => {
        if (cancelled) return;
        // Strip the param so reload-then-back doesn't re-open the
        // drawer. The rest of the URL (severity, library_id, etc.)
        // is preserved.
        sp.delete("file_id");
        const qs = sp.toString();
        const newUrl = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
        window.history.replaceState({}, "", newUrl);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filters: MediaFilters = useMemo(
    () => ({
      library_id: libraryId || undefined,
      category: category || undefined,
      search: search.trim() || undefined,
      // Stage 3 (audit follow-up): two changes to the severity
      // wiring:
      // (a) when the active set is non-empty AND smaller than
      //     the full vocabulary, send the comma-joined list as
      //     before;
      // (b) when the active set is empty (operator hit "hide
      //     all"), don't drop the param — that previously
      //     collapsed to "no filter" server-side and returned
      //     every row. Send ``severities_empty: true`` instead so
      //     the server returns zero rows, matching the operator's
      //     intent.
      severity:
        activeSevs.size > 0 && activeSevs.size < SEVERITY_KEYS.length
          ? Array.from(activeSevs).join(",")
          : undefined,
      severities_empty: activeSevs.size === 0 ? true : undefined,
      // Stage 31: comma-join the codec + container sets. Empty
      // set ⇒ undefined ⇒ no filter sent (server treats absent
      // as "all"). Sorting before join keeps the query string
      // stable across re-renders so React Query's cache key
      // doesn't churn just because Set iteration order differs.
      video_codec:
        activeCodecs.size > 0
          ? Array.from(activeCodecs).sort().join(",")
          : undefined,
      container:
        activeContainers.size > 0
          ? Array.from(activeContainers).sort().join(",")
          : undefined,
      // Stage 27's quarantined / include_quarantined params were
      // removed in Stage 05 (v1.7) — the workflow is gone; the
      // server no longer accepts them.
      // Stage 3 (audit follow-up): scope tri-state.
      // Send "media" / "non-media" verbatim; "all" is the
      // default-no-filter state so we omit the param to keep the
      // query string clean.
      scope: scope !== "all" ? scope : undefined,
      // Stage 3 (audit follow-up): turn on the matched-rules
      // join only when the column is actually visible. Saves the
      // join cost (and the extra response bytes) for operators
      // who hid the column. Keyed off the Files table's column
      // visibility so toggling the column flips the join on/off
      // without operator intervention.
      include_matched_rules: visibleColumns.includes("matched_rules")
        ? true
        : undefined,
      // Stage 13 (audit follow-up): same column-driven toggle for
      // the tags join. Off when the column is hidden — saves the
      // LEFT JOIN onto media_tags for the common case.
      include_tags: visibleColumns.includes("tags") ? true : undefined,
      sort: sort.key,
      sort_dir: sort.dir,
      // Stage 02 — per-column quick filters. Translate the
      // store's ``perColumnFilters`` map into the new backend
      // params. Empty / missing entries become undefined ⇒ no
      // param sent ⇒ no filter. Trim is applied so a stray space
      // doesn't fire a query.
      path_contains:
        perColumnFilters.filename && perColumnFilters.filename.trim()
          ? perColumnFilters.filename.trim()
          : undefined,
      codec_contains:
        perColumnFilters.codec && perColumnFilters.codec.trim()
          ? perColumnFilters.codec.trim()
          : undefined,
      container_eq:
        perColumnFilters.container && perColumnFilters.container.trim()
          ? perColumnFilters.container.trim()
          : undefined,
      extension_eq:
        perColumnFilters.extension && perColumnFilters.extension.trim()
          ? perColumnFilters.extension.trim()
          : undefined,
    }),
    [
      libraryId,
      category,
      search,
      activeSevs,
      activeCodecs,
      activeContainers,
      scope,
      sort,
      visibleColumns,
      perColumnFilters,
    ],
  );

  const list = useMediaList({
    ...filters,
    offset: page * pageSize,
    limit: pageSize,
  });

  // Reset selection whenever the visible page changes — see the
  // comment on the selected useState above.
  useEffect(() => {
    setSelected(new Set());
  }, [
    libraryId,
    category,
    search,
    activeSevs,
    activeCodecs,
    activeContainers,
    scope,
    sort,
    page,
    pageSize,
  ]);

  const totalPages = list.data
    ? Math.max(1, Math.ceil(list.data.total / pageSize))
    : 0;

  const visibleIds = useMemo(
    () => (list.data?.items ?? []).map((it) => it.id),
    [list.data],
  );
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const someVisibleSelected =
    !allVisibleSelected && visibleIds.some((id) => selected.has(id));

  function toggleSel(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearSelection() {
    setSelected(new Set());
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      return next;
    });
  }

  function clickSort(key: MediaSortKey) {
    setSort({
      key,
      dir: sort.key === key && sort.dir === "desc" ? "asc" : "desc",
    });
    setPage(0);
  }

  // ── filter wrappers that also reset page ─────────────────
  function setLibraryAndReset(v: string) {
    setLibraryId(v);
    setPage(0);
  }
  function setCategoryAndReset(v: string) {
    setCategory(v);
    setPage(0);
  }
  function setSearchAndReset(v: string) {
    setSearch(v);
    setPage(0);
  }
  function setScopeAndReset(v: ScopeMode) {
    setScope(v);
    setPage(0);
  }
  function toggleSev(key: SeverityKey) {
    const next = new Set(activeSevs);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setActiveSevs(next);
    setPage(0);
  }
  function allSevs() {
    setActiveSevs(new Set(SEVERITY_KEYS));
    setPage(0);
  }
  function noSevs() {
    setActiveSevs(new Set());
    setPage(0);
  }
  function toggleCodec(codec: string) {
    setActiveCodecs((prev) => {
      const next = new Set(prev);
      if (next.has(codec)) next.delete(codec);
      else next.add(codec);
      return next;
    });
    setPage(0);
  }
  function toggleContainer(container: string) {
    setActiveContainers((prev) => {
      const next = new Set(prev);
      if (next.has(container)) next.delete(container);
      else next.add(container);
      return next;
    });
    setPage(0);
  }
  function clearCodecsAndContainers() {
    setActiveCodecs(new Set());
    setActiveContainers(new Set());
    setPage(0);
  }

  return {
    libraries,
    triggerScan,
    triggerScanAll,
    resetLibraryScans,
    scanProgress,
    pageSize,
    sort,
    visibleColumns,
    toggleColumn,
    resetColumns,
    // Stage 02 — column resize + per-column filter.
    columnWidths,
    setColumnWidth,
    perColumnFilters,
    setPerColumnFilter,
    showColumnFilters,
    setShowColumnFilters,
    libraryId,
    setLibraryId: setLibraryAndReset,
    category,
    setCategory: setCategoryAndReset,
    search,
    setSearch: setSearchAndReset,
    scope,
    setScope: setScopeAndReset,
    activeSevs,
    toggleSev,
    allSevs,
    noSevs,
    activeCodecs,
    toggleCodec,
    activeContainers,
    toggleContainer,
    clearCodecsAndContainers,
    page,
    setPage,
    selected,
    toggleSel,
    clearSelection,
    toggleAllVisible,
    allVisibleSelected,
    someVisibleSelected,
    drawerFile,
    setDrawerFile,
    list,
    totalPages,
    clickSort,
  };
}
