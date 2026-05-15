/**
 * Stage 26 — library composition card.
 *
 * Renders the dashboard's "Categories" card, sectioned by the
 * ``group`` discriminator that the backend ships
 * (``video_codec`` and ``container`` today). Within each group,
 * rows are ordered by total_size_bytes descending — the
 * operational question is "what's eating my disk?".
 *
 * Each row shows a horizontal bar where width is the row's share
 * of total bytes within that group, plus file count and total
 * size.
 *
 * Stage 31: rows are now drill-down links. Clicking a row
 * navigates to ``/files?video_codec=<key>`` (or
 * ``?container=<key>``) which the Files page hydrates into its
 * codec/container filter. The earlier note about "drill-down by
 * codec is deferred" is now superseded — Stage 31 adds the
 * Files-side filter.
 *
 * ``unknown`` rows don't link because there's nothing useful
 * to filter on. An "unprobed" filter on Files would be its own
 * feature (the closest signal today is the absence of
 * ``video_codec`` on a row); deferred to a future stage.
 */

import { useMemo } from "react";
import { Link } from "react-router-dom";

import { Card, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  useDashboardCategories,
  type CategoryBreakdown,
  type CategoryGroup,
} from "@/hooks/useDashboard";
import { fmtBytes, fmtNum } from "@/lib/format";
import { useUiStore } from "@/stores/uiStore";

const GROUP_LABELS: Record<CategoryGroup, string> = {
  video_codec: "Video codec",
  container: "Container",
};

export function CategoriesCard() {
  const categories = useDashboardCategories(8);

  // Stage 11 audit fix (Issue 16): per-section collapse state.
  // Key ``categories`` matches DashboardPage's existing storage
  // scheme — same key would never collide because each section
  // owns its own key. Reading uiStore here keeps DashboardPage
  // from needing to pass props down to every card.
  const hidden = useUiStore((s) => s.dashboardHidden.includes("categories"));
  const toggle = useUiStore((s) => s.toggleDashboardSection);

  // Partition rows by group so we can render section headers and
  // compute per-group totals (the bar widths are relative to the
  // group's total, not the library-wide total — otherwise the
  // bars in the smaller group would look near-empty).
  const grouped = useMemo(() => {
    const out = new Map<CategoryGroup, CategoryBreakdown[]>();
    for (const row of categories.data ?? []) {
      const group = row.group;
      if (!out.has(group)) out.set(group, []);
      out.get(group)!.push(row);
    }
    return out;
  }, [categories.data]);

  return (
    <Card>
      <CardHead
        title="Categories"
        subtitle="Library composition by codec and container"
        actions={
          <button
            type="button"
            onClick={() => toggle("categories")}
            className="shrink-0 text-muted-2 hover:text-text"
            aria-label={hidden ? "Expand Categories" : "Collapse Categories"}
            aria-expanded={!hidden}
            title={hidden ? "Expand" : "Collapse"}
          >
            <Icon name={hidden ? "chev_right" : "chev_down"} size={14} />
          </button>
        }
      />
      {!hidden ? (
        <div className="categories-card-body">
          {categories.isLoading ? (
            <LoadingState label="Loading composition…" />
          ) : categories.isError ? (
            <ErrorState
              title="Failed to load composition"
              description={(categories.error as Error)?.message}
            />
          ) : grouped.size === 0 ? (
            <EmptyState
              icon="folder"
              title="No files yet"
              description="Add a library and run a scan to see codec / container breakdowns."
            />
          ) : (
            <>
              {(Object.keys(GROUP_LABELS) as CategoryGroup[]).map((group) => {
                const rows = grouped.get(group) ?? [];
                if (rows.length === 0) return null;
                const groupTotal = rows.reduce(
                  (acc, r) => acc + r.total_size_bytes,
                  0,
                );
                return (
                  <section
                    key={group}
                    className="categories-group"
                    aria-label={GROUP_LABELS[group]}
                  >
                    <h4 className="categories-group-label">
                      {GROUP_LABELS[group]}
                    </h4>
                    <ul className="m-0 p-0 list-none">
                      {rows.map((row) => (
                        <CategoryRow
                          key={`${row.group}:${row.key}`}
                          row={row}
                          groupTotal={groupTotal}
                        />
                      ))}
                    </ul>
                  </section>
                );
              })}
            </>
          )}
        </div>
      ) : null}
    </Card>
  );
}

function CategoryRow({
  row,
  groupTotal,
}: {
  row: CategoryBreakdown;
  groupTotal: number;
}) {
  // ``unknown`` rows get a muted styling. The bar is sized
  // relative to the group total, with a 1.5% floor so even
  // very-small rows show *something* — invisible bars are
  // confusing.
  const isUnknown = row.key === "unknown";
  const pct =
    groupTotal > 0
      ? Math.max((row.total_size_bytes / groupTotal) * 100, 1.5)
      : 0;
  // Stage 31: real rows deep-link to the Files page with the
  // codec/container filter pre-applied. ``unknown`` doesn't
  // link — there's nothing useful to filter on (an "unprobed"
  // filter would be a different feature, see deferred ledger).
  const body = (
    <>
      <div className="categories-row-label">
        <Icon
          name={isUnknown ? "alert" : "folder"}
          size={12}
          className={isUnknown ? "text-muted-2" : "text-muted"}
        />
        <span className="font-mono text-[12px]">{row.label}</span>
        {isUnknown ? (
          <span
            className="text-[10.5px] text-muted-2 uppercase tracking-[0.06em] font-semibold ml-1"
            title="Probe metadata missing or scanner couldn't read the file"
          >
            unprobed
          </span>
        ) : null}
      </div>
      <div className="categories-row-bar" aria-hidden="true">
        <div
          className="categories-row-bar-fill"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="categories-row-num font-mono">
        {fmtBytes(row.total_size_bytes)}
      </div>
      <div className="categories-row-count font-mono text-muted-2">
        {fmtNum(row.file_count)}
      </div>
    </>
  );

  if (isUnknown) {
    return <li className="categories-row">{body}</li>;
  }

  const href = `/files?${row.group}=${encodeURIComponent(row.key)}`;
  return (
    <li className="categories-row">
      <Link
        to={href}
        className="categories-row-link"
        title={`Filter Files by ${row.label}`}
      >
        {body}
      </Link>
    </li>
  );
}
