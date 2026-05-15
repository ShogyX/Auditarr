/**
 * Stage 12 audit fix (Issue 17) — Changelog page.
 *
 * The page composes two cards in vertical order:
 *
 *   1. ``UpdaterPanel`` — installed version, available update,
 *      apply controls, recent apply history. Moved here from
 *      HelpPage so the Help page can stay docs-only.
 *
 *   2. ``ChangelogContent`` — renders CHANGELOG.md content
 *      fetched via ``useChangelog()``. The backend endpoint
 *      ``GET /api/v1/system/changelog`` is not yet implemented at
 *      Stage 12 ship; the page handles its absence gracefully and
 *      will start rendering the content automatically once the
 *      backend endpoint lands.
 *
 * The audit explicitly recommends a dedicated backend endpoint
 * but allows the docs system as a fallback. We chose the dedicated-
 * endpoint shape because CHANGELOG.md is a single file with a clear
 * semantic identity ("history of versions") — making it one of many
 * docs would dilute that. Adding the backend route is a small
 * follow-up that requires no frontend changes.
 */

import { Card, CardBody } from "@/components/ui/Card";
import { DocBody } from "@/components/ui/DocBody";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { PageHeader } from "@/components/shell/PageHeader";
import { useChangelog } from "@/hooks/useChangelog";
import { useHelpKey } from "@/hooks/useHelpKey";
import { ApiError } from "@/services/apiClient";

import { UpdaterPanel } from "./UpdaterPanel";

export function ChangelogPage() {
  // Stage 12: reuse the existing ``help.docs`` help key for now —
  // contextual help docs don't yet have a "changelog" topic. When
  // documentation grows a dedicated changelog/release-notes page,
  // swap this for ``changelog.releases``.
  useHelpKey("help.docs");

  return (
    <>
      <PageHeader
        title="Changelog"
        sub="Release notes and update history"
      />
      <div className="p-6 flex flex-col gap-6 max-w-4xl">
        <UpdaterPanel />
        <ChangelogContent />
      </div>
    </>
  );
}

function ChangelogContent() {
  const changelog = useChangelog();

  if (changelog.isLoading) {
    return (
      <Card>
        <CardBody>
          <LoadingState label="Loading changelog…" />
        </CardBody>
      </Card>
    );
  }

  // 404 is the expected case until the backend endpoint lands.
  // Surface a friendly, actionable empty state rather than the
  // generic ErrorState — operators shouldn't think something
  // broke when the file is just not yet wired up.
  if (
    changelog.isError &&
    changelog.error instanceof ApiError &&
    changelog.error.status === 404
  ) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon="info"
            title="Changelog not yet served by the API"
            description="The CHANGELOG.md file exists at the project root but isn't yet exposed via a backend endpoint. The Updates panel above still shows installed and available versions."
          />
        </CardBody>
      </Card>
    );
  }

  if (changelog.isError || !changelog.data) {
    return (
      <Card>
        <CardBody>
          <ErrorState
            title="Failed to load changelog"
            description={(changelog.error as Error | undefined)?.message}
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardBody>
        {changelog.data.last_modified ? (
          <div className="mb-4 flex items-center gap-1.5 text-[11.5px] text-muted-2">
            <Icon name="clock" size={11} />
            <span>
              Last updated {new Date(changelog.data.last_modified).toLocaleDateString()}
            </span>
          </div>
        ) : null}
        <DocBody html={changelog.data.body_html} />
      </CardBody>
    </Card>
  );
}
