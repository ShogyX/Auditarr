/**
 * Stage 3 — Files page.
 *
 * Slim orchestrator. State, derivations, and handlers live in
 * ``useFilesPageState``. The page itself composes:
 *
 *   - ``PageHeader``           — title / subtitle / scan progress / Run scan
 *   - ``FilesScopeBar``        — severity scope toggle + chip row
 *   - ``FilesToolbar``         — filters + selection action bar
 *   - ``FilesTable``           — the sortable, selectable table
 *   - ``FilesPager``           — page navigation
 *   - ``FileDetailDrawer``     — per-file detail panel
 *
 * The Stage-1 ``DataGrid`` primitive is intentionally NOT adopted in
 * this stage. Adopting it would change the rendered table DOM and
 * require updating 30+ tests across four files. That migration is
 * Stage 3b, gated on a Playwright visual-diff baseline.
 *
 * Stage 1 ``Page`` primitive is also not adopted yet because the
 * existing ``test-pages.test.tsx`` smoke-tests assume the page renders
 * a ``PageHeader`` directly. Migrating to ``Page`` will land alongside
 * the Stage 3b table refactor.
 *
 * Pre-Stage-3:  1318 LOC
 * Post-Stage-3:  ~85 LOC (this file)
 * Sub-modules:   filesShared, useFilesPageState, FilesScopeBar,
 *                FilesToolbar, FilesSelectionActions,
 *                FilesOptimizeProfilePicker, FilesTable,
 *                FilesPager, FilesRunScanButton
 */

import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardBody } from "@/components/ui/Card";
import { ScanProgressBar } from "@/components/ui/ScanProgressBar";
import { EmptyState } from "@/components/ui/States";
import { useHelpKey } from "@/hooks/useHelpKey";

import { FileDetailDrawer } from "./FileDetailDrawer";
import { FilesPager } from "./FilesPager";
import { FilesRunScanButton } from "./FilesRunScanButton";
import { FilesScopeBar } from "./FilesScopeBar";
import { FilesTable } from "./FilesTable";
import { FilesToolbar } from "./FilesToolbar";
import { useFilesPageState } from "./useFilesPageState";

export function FilesPage() {
  useHelpKey("rules.conditions");
  const s = useFilesPageState();

  return (
    <>
      <PageHeader
        title="Files"
        sub="Browse, filter, and inspect every file Auditarr has indexed"
        helpKey="rules.conditions"
        actions={
          <>
            {/* Stage 8 (audit follow-up): the inline Pill was a
                yes/no spinner; the new ScanProgressBar shows actual
                progress (files_seen / files_total_estimate +
                percent) driven by ``scan.progress`` WS events. Falls
                back to the indeterminate visual until the scanner
                finishes ``_enumerate`` and emits a total estimate. */}
            <ScanProgressBar />
            <FilesRunScanButton
              libraryId={s.libraryId}
              disabled={!s.libraryId || s.triggerScan.isPending}
              onRun={(id) => s.triggerScan.mutate({ libraryId: id })}
              onScanAll={() => s.triggerScanAll.mutate({})}
              isPending={s.triggerScan.isPending || s.triggerScanAll.isPending}
            />
          </>
        }
      />
      <div className="p-6 flex flex-col gap-4">
        <FilesScopeBar
          scope={s.scope}
          onScope={s.setScope}
          activeSevs={s.activeSevs}
          onToggleSev={s.toggleSev}
          onAll={s.allSevs}
          onNone={s.noSevs}
        />

        {(s.libraries.data?.length ?? 0) === 0 ? (
          <Card>
            <CardBody>
              <EmptyState
                icon="files"
                title="No libraries configured"
                description="Add a library in Settings to start scanning. Auditarr indexes files, classifies them, and runs ffprobe on media candidates."
              />
            </CardBody>
          </Card>
        ) : (
          <Card>
            <FilesToolbar
              libraries={s.libraries.data ?? []}
              libraryId={s.libraryId}
              onLibrary={s.setLibraryId}
              category={s.category}
              onCategory={s.setCategory}
              quarantineView={s.quarantineView}
              onQuarantineView={s.setQuarantineView}
              search={s.search}
              onSearch={s.setSearch}
              activeCodecs={s.activeCodecs}
              activeContainers={s.activeContainers}
              onToggleCodec={s.toggleCodec}
              onToggleContainer={s.toggleContainer}
              onClearCodecsAndContainers={s.clearCodecsAndContainers}
              visibleColumns={s.visibleColumns}
              onToggleColumn={s.toggleColumn}
              onResetColumns={s.resetColumns}
              total={s.list.data?.total ?? 0}
              shown={s.list.data?.items.length ?? 0}
              selectionCount={s.selected.size}
              onClearSelection={s.clearSelection}
              selectedIds={s.selected}
            />

            <FilesTable
              list={s.list}
              visibleColumns={s.visibleColumns}
              sort={s.sort}
              onSort={s.clickSort}
              selected={s.selected}
              onToggleSel={s.toggleSel}
              onToggleAll={s.toggleAllVisible}
              allVisibleSelected={s.allVisibleSelected}
              someVisibleSelected={s.someVisibleSelected}
              onOpenDrawer={s.setDrawerFile}
            />
            {s.totalPages > 1 ? (
              <FilesPager
                page={s.page}
                totalPages={s.totalPages}
                onPage={s.setPage}
              />
            ) : null}
          </Card>
        )}
      </div>

      {s.drawerFile ? (
        <FileDetailDrawer
          file={s.drawerFile}
          onClose={() => s.setDrawerFile(null)}
        />
      ) : null}
    </>
  );
}
