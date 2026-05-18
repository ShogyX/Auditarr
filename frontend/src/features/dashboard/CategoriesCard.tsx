/**
 * Categories card — v1.9 Stage 3.3 redesign.
 *
 * Pre-1.9 this card was a pair of bar graphs (video_codec, container).
 * Useful at a glance, but not actionable — operators kept asking the
 * questions the card didn't answer:
 *
 *   * "How many of my files are 4K vs 1080p?"
 *   * "What languages of audio / subtitles do I have?"
 *   * "How many files have NO subtitles?"
 *   * "What's the median bitrate of my 1080p HEVC content vs h264?"
 *
 * The redesigned card answers each of those with its own section:
 *
 *   1. Resolutions       — counts per bucket (<480p, 480p, 720p, 1080p,
 *      1440p, 4K, 8K, Unknown).
 *   2. Extensions        — top 8 by file count (file ext, not container).
 *   3. Containers        — normalized labels (MKV, MP4, WEBM, …) merged
 *      across raw ffprobe demuxer aliases via ``containerLabel``.
 *   4. Subtitle formats  — SRT, ASS, PGS, VobSub, … from probed
 *      subtitle codecs.
 *   5. Subtitle languages.
 *   6. Audio languages.
 *   7. Unknown tracks    — files that probed successfully but came back
 *      with NULL video_codec or audio_codec (a probe-stage health signal).
 *   8. Internal vs external subtitles — file with probed subtitle stream
 *      vs separate sidecar .srt / .ass / etc.
 *   9. Orphan count      — media files marked is_orphaned (file missing
 *      from disk since last scan).
 *  10. Bitrate matrix    — per-(library, resolution, codec, container)
 *      cell, median bitrate + file count.
 *
 * Single API call to ``/dashboard/composition``; the card stays
 * presentational. The card-disable + collapse + reorder wiring is
 * unchanged from the pre-1.9 version.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { Card, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { DashboardCardMenu } from "./DashboardCardMenu";
import { useDashboardCardDisabled } from "@/hooks/useDashboardCardDisabled";
import {
  useDashboardComposition,
  type CompositionRow,
  type BitrateMatrixRow,
} from "@/hooks/useDashboard";
import { fmtBytes, fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { useUiStore } from "@/stores/uiStore";

export function CategoriesCard() {
  const comp = useDashboardComposition(null);

  const hidden = useUiStore((s) =>
    s.dashboardHidden.includes("categories"),
  );
  // v1.9 Stage 2.5 — uniform helper.
  const [disabled] = useDashboardCardDisabled("categories");
  const toggle = useUiStore((s) => s.toggleDashboardSection);

  // Early-return AFTER all hooks (react-hooks/rules-of-hooks
  // requires the hook count to be stable across renders).
  if (disabled) return null;

  const data = comp.data;
  const empty =
    !!data &&
    data.resolutions.length === 0 &&
    data.extensions.length === 0 &&
    data.containers.length === 0 &&
    data.subtitle_languages.length === 0 &&
    data.audio_languages.length === 0 &&
    data.orphan_count === 0;

  return (
    <Card>
      <CardHead
        title="Categories"
        subtitle="Library composition: resolutions, languages, containers"
        actions={
          <>
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
            <DashboardCardMenu cardKey="categories" />
          </>
        }
      />
      {!hidden ? (
        <div className="px-4 py-3 flex flex-col gap-4">
          {comp.isLoading ? (
            <LoadingState label="Loading composition…" />
          ) : comp.isError ? (
            <ErrorState
              title="Failed to load composition"
              description={(comp.error as Error)?.message}
            />
          ) : !data || empty ? (
            <EmptyState
              icon="folder"
              title="No media yet"
              description="Add a library and run a scan to see the composition breakdown."
            />
          ) : (
            <>
              {data.resolutions.length > 0 ? (
                <CountRowSection
                  title="Resolutions"
                  rows={data.resolutions}
                  withSize
                  hrefBuilder={() =>
                    // The Files page doesn't filter on a resolution
                    // bucket yet — Stage 3.1 introduces per-column
                    // filtering which will own width/height. For now
                    // these rows are read-only.
                    null
                  }
                />
              ) : null}

              {data.extensions.length > 0 ? (
                <CountRowSection
                  title="Top extensions"
                  rows={data.extensions}
                  withSize
                  hrefBuilder={(r) =>
                    // The Files page DOES filter on extension — link
                    // through so the operator can drill into ".srt"
                    // (if it slips into the media count via category=media)
                    // or any other extension. The ``(none)`` placeholder
                    // sticks to a non-link.
                    r.key === "(none)" ? null : `/files?extension=${r.key}`
                  }
                />
              ) : null}

              {data.containers.length > 0 ? (
                <CountRowSection
                  title="Containers"
                  rows={data.containers}
                  withSize
                  hrefBuilder={(r) =>
                    // The Files page's container filter is the
                    // Stage 31 multi-select. We use the raw key
                    // (lowercase) which matches what the table
                    // filter accepts (it's case-insensitive).
                    `/files?container=${encodeURIComponent(r.key)}`
                  }
                />
              ) : null}

              {data.subtitle_formats.length > 0 ? (
                <CountRowSection
                  title="Subtitle formats"
                  rows={data.subtitle_formats}
                />
              ) : null}

              {data.subtitle_languages.length > 0 ? (
                <CountRowSection
                  title="Subtitle languages"
                  rows={data.subtitle_languages}
                />
              ) : null}

              {data.audio_languages.length > 0 ? (
                <CountRowSection
                  title="Audio languages"
                  rows={data.audio_languages}
                />
              ) : null}

              <UnknownTracksRow data={data.unknown_tracks} />

              <SubtitlesInternalExternalRow
                data={data.subtitles_internal_external}
              />

              {data.orphan_count > 0 ? (
                <section aria-label="Orphan count">
                  <SectionHeading>Orphan files</SectionHeading>
                  <div className="text-[12.5px] text-muted-2 mt-1">
                    <Link
                      to="/files?orphaned=true"
                      className="text-sev-warn hover:underline"
                    >
                      {fmtNum(data.orphan_count)} media file
                      {data.orphan_count === 1 ? "" : "s"}
                    </Link>{" "}
                    were missing on disk on the last scan.
                  </div>
                </section>
              ) : null}

              {data.bitrate_matrix.length > 0 ? (
                <BitrateMatrix rows={data.bitrate_matrix} />
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </Card>
  );
}

// ── Section primitives ─────────────────────────────────────────

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-[11.5px] uppercase tracking-wide text-muted-2 font-semibold m-0">
      {children}
    </h4>
  );
}

function CountRowSection({
  title,
  rows,
  withSize = false,
  hrefBuilder,
}: {
  title: string;
  rows: CompositionRow[];
  withSize?: boolean;
  hrefBuilder?: (row: CompositionRow) => string | null;
}) {
  return (
    <section aria-label={title}>
      <SectionHeading>{title}</SectionHeading>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1.5">
        {rows.map((r) => {
          const href = hrefBuilder?.(r) ?? null;
          const inner = (
            <span className="inline-flex items-center gap-1.5 text-[12px]">
              <span className="font-medium">{r.label}</span>
              <span className="text-muted-2">
                {fmtNum(r.count)}
                {withSize && r.total_size_bytes > 0
                  ? ` · ${fmtBytes(r.total_size_bytes)}`
                  : ""}
              </span>
            </span>
          );
          return href ? (
            <Link
              key={r.key}
              to={href}
              className="px-2 py-0.5 rounded-md bg-surface-2 hover:bg-[var(--hover)] transition-colors"
            >
              {inner}
            </Link>
          ) : (
            <span
              key={r.key}
              className="px-2 py-0.5 rounded-md bg-surface-2"
            >
              {inner}
            </span>
          );
        })}
      </div>
    </section>
  );
}

function UnknownTracksRow({
  data,
}: {
  data: { video_unknown_count: number; audio_unknown_count: number };
}) {
  const total = data.video_unknown_count + data.audio_unknown_count;
  if (total === 0) return null;
  return (
    <section aria-label="Unknown tracks">
      <SectionHeading>Unknown tracks</SectionHeading>
      <div className="mt-1 text-[12.5px] text-muted-2">
        {data.video_unknown_count > 0 ? (
          <span className="mr-3">
            <strong className="text-text-2">{fmtNum(data.video_unknown_count)}</strong>{" "}
            files with no video codec
          </span>
        ) : null}
        {data.audio_unknown_count > 0 ? (
          <span>
            <strong className="text-text-2">{fmtNum(data.audio_unknown_count)}</strong>{" "}
            files with no audio codec
          </span>
        ) : null}
      </div>
    </section>
  );
}

function SubtitlesInternalExternalRow({
  data,
}: {
  data: { internal: number; external: number };
}) {
  if (data.internal === 0 && data.external === 0) return null;
  return (
    <section aria-label="Internal vs external subtitles">
      <SectionHeading>Subtitles · internal vs external</SectionHeading>
      <div className="mt-1 text-[12.5px] text-muted-2 flex gap-4">
        <span>
          <strong className="text-text-2">{fmtNum(data.internal)}</strong>{" "}
          embedded
        </span>
        <span>
          <strong className="text-text-2">{fmtNum(data.external)}</strong>{" "}
          sidecar files
        </span>
      </div>
    </section>
  );
}

// v1.9 Stage 9.5.6 (OP-7) — sortable median-bitrate matrix.
// Operators reported they wanted to scan for the slowest-encoded
// rows; sorting by median bitrate descending puts heavy
// transcode-candidates at the top. The four other columns
// (library, codec, container, files) also sort; click a header
// to flip direction.
type BitrateSortKey =
  | "library"
  | "resolution"
  | "codec"
  | "container"
  | "files"
  | "median";
type SortDir = "asc" | "desc";

function compareBitrateRows(
  a: BitrateMatrixRow,
  b: BitrateMatrixRow,
  key: BitrateSortKey,
): number {
  // Sort helpers — null/undefined sink to the end on asc.
  function cmpStr(x: string | null | undefined, y: string | null | undefined) {
    const xx = x ?? "\uffff";
    const yy = y ?? "\uffff";
    return xx.localeCompare(yy);
  }
  switch (key) {
    case "library":
      return cmpStr(a.library_name, b.library_name);
    case "resolution":
      return cmpStr(a.resolution_key, b.resolution_key);
    case "codec":
      return cmpStr(a.video_codec, b.video_codec);
    case "container":
      return cmpStr(a.container, b.container);
    case "files":
      return a.file_count - b.file_count;
    case "median":
      return a.median_bitrate_kbps - b.median_bitrate_kbps;
  }
}

function BitrateMatrix({ rows }: { rows: BitrateMatrixRow[] }) {
  const [sortKey, setSortKey] = useState<BitrateSortKey>("median");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function onHeaderClick(key: BitrateSortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      // First click on a new column: default to descending for
      // numeric columns (highest first is usually the useful
      // view), ascending for strings.
      setSortDir(key === "files" || key === "median" ? "desc" : "asc");
    }
  }

  const sortedRows = useMemo(() => {
    const out = [...rows];
    out.sort((a, b) => {
      const cmp = compareBitrateRows(a, b, sortKey);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [rows, sortKey, sortDir]);

  function SortHeader({
    label,
    sortableKey,
    rightAlign,
  }: {
    label: string;
    sortableKey: BitrateSortKey;
    rightAlign?: boolean;
  }) {
    const active = sortKey === sortableKey;
    return (
      <th
        className={cn(
          "font-normal pr-3 py-1 cursor-pointer select-none",
          rightAlign && "text-right",
          active && "text-fg",
        )}
        onClick={() => onHeaderClick(sortableKey)}
        data-testid={`bitrate-sort-${sortableKey}`}
        aria-sort={
          active ? (sortDir === "asc" ? "ascending" : "descending") : "none"
        }
      >
        <span className="inline-flex items-center gap-0.5">
          {label}
          {active ? (
            <span className="text-[10px]">
              {sortDir === "asc" ? "▲" : "▼"}
            </span>
          ) : null}
        </span>
      </th>
    );
  }

  return (
    <section aria-label="Median bitrate matrix">
      <SectionHeading>Median bitrate</SectionHeading>
      <div className="mt-1.5 overflow-x-auto">
        <table className="text-[12px] w-full border-collapse">
          <thead>
            <tr className="text-muted-2 text-left">
              <SortHeader label="Library" sortableKey="library" />
              <SortHeader label="Resolution" sortableKey="resolution" />
              <SortHeader label="Codec" sortableKey="codec" />
              <SortHeader label="Container" sortableKey="container" />
              <SortHeader label="Files" sortableKey="files" rightAlign />
              <SortHeader label="Median" sortableKey="median" rightAlign />
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((r, i) => {
              // v1.9 Stage 9.5.6 (OP-7) — each row is a deep
              // link to the Files page filtered by the row's
              // codec and container. Empty cells (null codec
              // or container) render as a plain row — the
              // operator can't usefully filter on "no codec".
              const canLink = !!r.video_codec || !!r.container;
              const filters = [
                r.video_codec
                  ? `video_codec=${encodeURIComponent(r.video_codec)}`
                  : null,
                r.container
                  ? `container=${encodeURIComponent(r.container)}`
                  : null,
              ].filter(Boolean);
              const href = canLink ? `/files?${filters.join("&")}` : null;
              const content = (
                <>
                  <td className="pr-3 py-1">{r.library_name ?? "—"}</td>
                  <td className="pr-3 py-1">{r.resolution_key}</td>
                  <td className="pr-3 py-1">{r.video_codec ?? "—"}</td>
                  <td className="pr-3 py-1">{r.container ?? "—"}</td>
                  <td className="pr-3 py-1 text-right">
                    {fmtNum(r.file_count)}
                  </td>
                  <td className="py-1 text-right">
                    <span>
                      {(r.median_bitrate_kbps / 1000).toFixed(1)} Mbps
                    </span>
                    <span className="text-muted-2 ml-1">
                      ({fmtNum(r.median_bitrate_kbps)} kbps)
                    </span>
                  </td>
                </>
              );
              return href ? (
                <tr
                  key={i}
                  className="border-t border-border hover:bg-[var(--hover)] cursor-pointer transition-colors"
                  onClick={(e) => {
                    // Plain td-content rows; navigate via window.location
                    // since we're already inside react-router we just
                    // use a Link wrapper on the table-as-block click
                    // surface via the data-href attr below.
                    const target = e.currentTarget.dataset.href;
                    if (target) window.location.href = target;
                  }}
                  data-href={href}
                  data-testid={`bitrate-row-link-${r.resolution_key}`}
                >
                  {content}
                </tr>
              ) : (
                <tr key={i} className="border-t border-border">
                  {content}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
