/**
 * File detail drawer (Stage 23).
 *
 * Slides in from the right when a row is clicked. Renders:
 *
 *   - file metadata: size, codec, resolution, mtime, container,
 *     duration, audio/subtitle tracks
 *   - matched rules (live data from /media/{id}/evaluations) with
 *     rule names, severities, and a "no longer active" marker for
 *     evaluations whose rules have since been disabled
 *   - the raw ffprobe blob in a collapsible mono block, only when
 *     it's actually present
 *
 * Activity log is deliberately NOT shown — there is no per-file
 * audit table in the data model today (the existing audit_logs is
 * event-keyed). The prototype mocks one; we leave it out rather
 * than fake it, per the Stage 22 directive's "no UI-derived state"
 * rule.
 */

import { useEffect } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import { LoadingState } from "@/components/ui/States";
import { fmtBytes, fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { toast } from "@/lib/toast";
import {
  useMediaDetail,
  useMediaEvaluations,
  useMediaTags,
  useReprobeMedia,
  type MediaFileDetail,
  type MediaFileSummary,
  type MediaTag,
} from "@/hooks/useMedia";
import { usePlaybackEvents } from "@/hooks/usePlayback";

interface FileDetailDrawerProps {
  /** Summary row clicked in the table; we use it for instant-render
   *  while the full detail loads, so the drawer never shows a blank
   *  shell. */
  file: MediaFileSummary;
  onClose: () => void;
}

export function FileDetailDrawer({ file, onClose }: FileDetailDrawerProps) {
  const detail = useMediaDetail(file.id);
  const evals = useMediaEvaluations(file.id);
  // Stage 12 (audit follow-up): playback history per file.
  // Hook is enabled only when the drawer is rendered for a real
  // file id; the hook itself short-circuits when mediaFileId is
  // null, so the network call only fires when we actually have a
  // target.
  const playback = usePlaybackEvents({
    mediaFileId: file.id,
    limit: 10,
  });
  // Stage 13 (audit follow-up): per-file tags. Same disabled-when-
  // no-id pattern as the other per-file hooks. Section is hidden
  // entirely when the file has no tags — mirroring the playback
  // pattern.
  const tagsQuery = useMediaTags(file.id);

  // Stage 27: per-file mutations. Wired here so the operator can
  // refresh probe data without leaving the drawer. The Stage 27
  // quarantine + unquarantine hooks that lived here are gone with
  // Stage 05 (v1.7) — Section A.0 "delete means delete".
  const reprobe = useReprobeMedia();

  // Close on Escape — keyboard parity with the prototype's drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // ``d`` is the rendering view of the file's summary-shaped fields —
  // those are guaranteed present from the moment the drawer opens.
  // ``full`` is the detail-fetch result (probe blob, durations, etc.)
  // and is undefined during the initial load; everything that reads
  // it is null-guarded with optional chaining.
  const d: MediaFileSummary = file;
  const full: MediaFileDetail | undefined = detail.data;
  const lastDir = file.path.split("/").slice(0, -1).join("/");

  // Stage 27's ``isQuarantined`` derived state is gone — the
  // quarantine columns have been removed (Stage 05, Section A.0).

  async function runReprobe() {
    try {
      const updated = await reprobe.mutateAsync(file.id);
      if (updated.is_orphaned) {
        toast(`File is missing on disk — marked orphan`, "warn");
      } else if (updated.probe_failed) {
        toast(
          `Re-probe failed: ${updated.probe_error ?? "unknown error"}`,
          "warn",
        );
      } else {
        toast("Re-probed successfully", "ok");
      }
    } catch (err) {
      toast(
        `Re-probe failed: ${err instanceof Error ? err.message : String(err)}`,
        "error",
        5000,
      );
    }
  }

  // Stage 27's ``runQuarantine`` + ``runUnquarantine`` handlers
  // lived here. Stage 05 (v1.7) retired the quarantine workflow
  // (Section A.0); the drawer no longer offers those buttons.

  return (
    <>
      <div className="file-drawer-backdrop" onClick={onClose} />
      <aside
        className="file-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Details for ${file.filename}`}
      >
        <div className="file-drawer-head">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-mono text-muted-2 truncate">
              {lastDir || "/"}
            </div>
            <h2 className="font-mono text-[15px] font-semibold m-0 truncate">
              {file.filename}
            </h2>
            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
              <Pill sev={d.severity}>{d.severity}</Pill>
              <Pill>{d.category}</Pill>
              {d.is_orphaned ? <Pill sev="warn">orphaned</Pill> : null}
              {/* Stage 27 also rendered a "quarantined" Pill here;
                  Stage 05 (v1.7) removed it. */}
            </div>
            {/* Stage 27 surfaced a "Quarantine reason: ..." line
                here when the file was quarantined; that block is
                gone with the workflow. */}
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label="Close detail drawer"
          >
            <Icon name="x" size={14} />
          </Button>
        </div>

        <div className="file-drawer-body">
          <div className="file-meta-grid">
            <MetaCell label="Size" value={fmtBytes(d.size_bytes)} />
            <MetaCell
              label="Resolution"
              value={d.width && d.height ? `${d.width}×${d.height}` : "—"}
            />
            <MetaCell
              label="Video codec"
              value={d.video_codec ?? "—"}
            />
            <MetaCell
              label="Audio codec"
              value={d.audio_codec ?? "—"}
            />
            <MetaCell
              label="Container"
              value={full?.container ?? "—"}
            />
            <MetaCell
              label="Duration"
              value={
                full?.duration_seconds
                  ? fmtDuration(full.duration_seconds)
                  : "—"
              }
            />
            <MetaCell
              label="Bitrate"
              value={
                full?.bitrate_kbps
                  ? `${fmtNum(full.bitrate_kbps)} kbps`
                  : "—"
              }
            />
            <MetaCell
              label="Subtitles"
              value={d.has_subtitles ? "yes" : "—"}
            />
          </div>

          {/* Language tracks if known. Both arrays are detail-only,
              so we gate on ``full`` first. */}
          {full &&
          ((full.audio_languages?.length ?? 0) > 0 ||
            (full.subtitle_languages?.length ?? 0) > 0) ? (
            <div className="file-drawer-section">
              <h3 className="file-drawer-section-head">Tracks</h3>
              <div className="flex flex-col gap-1 px-3 py-2 text-[12.5px]">
                {(full.audio_languages?.length ?? 0) > 0 ? (
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-muted-2 text-[11px] uppercase tracking-wide">
                      audio
                    </span>
                    {full.audio_languages?.map((lang) => (
                      <Tag key={`a-${lang}`}>{lang}</Tag>
                    ))}
                  </div>
                ) : null}
                {(full.subtitle_languages?.length ?? 0) > 0 ? (
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-muted-2 text-[11px] uppercase tracking-wide">
                      subs
                    </span>
                    {full.subtitle_languages?.map((lang) => (
                      <Tag key={`s-${lang}`}>{lang}</Tag>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {/* Matched rules */}
          <div className="file-drawer-section">
            <div className="file-drawer-section-head">
              <span>Matched rules</span>
              <span className="text-muted text-[11.5px] font-normal">
                {evals.isLoading
                  ? "loading…"
                  : `${evals.data?.length ?? 0}`}
              </span>
            </div>
            {evals.isLoading ? (
              <div className="px-3 py-4">
                <LoadingState label="Loading evaluations…" />
              </div>
            ) : (evals.data?.length ?? 0) === 0 ? (
              <p className="px-3 py-3 text-[12.5px] text-muted italic m-0">
                No rules matched this file. Run an evaluation, or check
                whether any rules are enabled.
              </p>
            ) : (
              <ul className="m-0 p-0 list-none">
                {evals.data!.map((ev) => (
                  <li
                    key={ev.rule_id}
                    className="flex items-center justify-between gap-2 px-3 py-2 border-t border-border first:border-t-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] truncate flex items-center gap-1.5">
                        {ev.rule_name}
                        {!ev.rule_enabled ? (
                          <span
                            className="text-[10.5px] uppercase tracking-wide text-muted-2 font-semibold"
                            title="This rule has been disabled since the evaluation ran"
                          >
                            inactive
                          </span>
                        ) : null}
                      </div>
                      <div className="text-[11px] text-muted-2 font-mono">
                        {formatActionsSummary(ev.actions_summary)}
                      </div>
                    </div>
                    <Pill sev={ev.severity}>{ev.severity}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Stage 13 (audit follow-up): tags grouped by source.
              Hidden entirely when the file has no tags — same
              "no noisy empty state" pattern as the playback
              section below. Casing is preserved (Sonarr "4K"
              and rule "4k" render as distinct chips). */}
          {tagsQuery.data && tagsQuery.data.length > 0 ? (
            <FileTagsSection tags={tagsQuery.data} />
          ) : null}

          {/* Stage 19 (audit follow-up): Security section.
              Hidden when both hash + VT result are null (the
              majority of legacy rows). Renders the hash with a
              short visual badge + the VT result counts. */}
          {full && (full.hash_sha256 || full.virustotal_result) ? (
            <div
              className="file-drawer-section"
              data-testid="file-drawer-security"
            >
              <div className="file-drawer-section-head">
                <span>Security</span>
              </div>
              <div className="flex flex-col gap-1.5 px-3 py-2 text-[12.5px]">
                {full.hash_sha256 ? (
                  <div className="flex items-center gap-2">
                    <span className="text-muted-2">SHA-256</span>
                    <code className="font-mono text-[11px] truncate flex-1">
                      {full.hash_sha256}
                    </code>
                  </div>
                ) : null}
                {full.virustotal_result ? (
                  <div className="flex items-center gap-2">
                    <span className="text-muted-2">VirusTotal</span>
                    {full.virustotal_result.status === "not_found" ? (
                      <Pill>unknown</Pill>
                    ) : (
                      <>
                        {full.virustotal_result.malicious > 0 ? (
                          <Pill sev="error">
                            malicious: {full.virustotal_result.malicious}
                          </Pill>
                        ) : null}
                        {full.virustotal_result.suspicious > 0 ? (
                          <Pill sev="warn">
                            suspicious: {full.virustotal_result.suspicious}
                          </Pill>
                        ) : null}
                        {full.virustotal_result.malicious === 0 &&
                        full.virustotal_result.suspicious === 0 ? (
                          <Pill sev="ok">clean</Pill>
                        ) : null}
                        <a
                          href={full.virustotal_result.permalink}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-1 text-[11.5px] underline text-muted-2"
                        >
                          report ↗
                        </a>
                      </>
                    )}
                  </div>
                ) : null}
                {full.virustotal_checked_at ? (
                  <div className="text-[11px] text-muted-2 pl-1">
                    checked{" "}
                    {new Date(full.virustotal_checked_at).toLocaleString()}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {/* Stage 12 (audit follow-up): playback history.
              Hidden entirely when there are no events for this file
              — per the plan, no noisy empty state. Most files in a
              healthy library will have zero events, so showing a
              perpetual "no playback yet" line on every drawer would
              be more noise than signal. */}
          {playback.data && playback.data.items.length > 0 ? (
            <div className="file-drawer-section">
              <div className="file-drawer-section-head">
                <span>Playback history</span>
                <span className="text-muted text-[11.5px] font-normal">
                  {playback.data.total}
                  {playback.data.total > playback.data.items.length
                    ? ` (showing ${playback.data.items.length})`
                    : ""}
                </span>
              </div>
              <ul className="m-0 p-0 list-none">
                {playback.data.items.map((event) => (
                  <li
                    key={event.id}
                    className="flex items-center justify-between gap-2 px-3 py-2 border-t border-border first:border-t-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-[12.5px] truncate">
                        <span className="text-muted-2 font-mono mr-2">
                          {new Date(event.started_at).toLocaleString()}
                        </span>
                        {event.device_name ?? event.device_kind ?? "unknown device"}
                      </div>
                      {/* reason_code only renders when present AND
                          the decision is one of the ones where it
                          matters (transcode or failed). The poller
                          can attach reason_code to direct_play
                          events too — for those it's usually noise. */}
                      {event.reason_code &&
                      (event.decision === "transcode" ||
                        event.decision === "failed") ? (
                        <div className="text-[11px] text-muted-2 font-mono">
                          {event.reason_code}
                        </div>
                      ) : null}
                    </div>
                    <Tag>{event.decision}</Tag>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Raw probe (collapsible — only render when present).
              ``probe`` and ``probe_failed`` are detail-only fields. */}
          {full?.probe ? (
            <div className="file-drawer-section">
              <div className="file-drawer-section-head">
                <span>ffprobe</span>
                <button
                  type="button"
                  className="text-[11.5px] text-muted hover:text-text"
                  onClick={() =>
                    copyToClipboard(JSON.stringify(full.probe, null, 2))
                  }
                  title="Copy JSON to clipboard"
                >
                  <Icon name="duplicate" size={11} className="inline" /> copy
                </button>
              </div>
              <pre className="file-probe-pre">
                {JSON.stringify(full.probe, null, 2)}
              </pre>
            </div>
          ) : full?.probe_failed ? (
            <div className="file-drawer-section">
              <div className="file-drawer-section-head">ffprobe</div>
              <div className="px-3 py-2 text-[12.5px]">
                <span className="text-sev-error">probe failed</span>
                {full.probe_error ? (
                  <div className="text-muted-2 font-mono text-[11px] mt-1">
                    {full.probe_error}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>

        <div className="file-drawer-foot">
          <span
            className="text-[11.5px] text-muted-2 font-mono truncate flex-1"
            title={file.path}
          >
            {file.path}
          </span>
          <Button size="sm" onClick={() => copyToClipboard(file.path)}>
            <Icon name="duplicate" size={12} /> Copy path
          </Button>
          {/* Stage 27: per-file Re-probe. The Stage 27 Quarantine /
              Restore button block that sat next to this — flipping
              based on isQuarantined — is removed with Stage 05
              (Section A.0 "delete means delete"). */}
          <Button
            size="sm"
            onClick={runReprobe}
            disabled={reprobe.isPending}
            title="Re-run ffprobe on this file — refreshes codec/container metadata without a full library scan"
          >
            <Icon
              name="refresh"
              size={12}
              className={reprobe.isPending ? "animate-spin" : undefined}
            />
            {reprobe.isPending ? "Re-probing…" : "Re-probe"}
          </Button>
        </div>
      </aside>
    </>
  );
}

/**
 * Stage 13 (audit follow-up): tag chips grouped by ``source``.
 * Sources we know about get human labels ("From rules", "From
 * Sonarr", "Manual"); unknown sources fall back to a capitalized
 * version of their key so a future integration shipping a new
 * source string doesn't render as the raw token.
 *
 * Casing of NAMES is preserved exactly — per the plan's guard
 * rail, "4K" and "4k" must remain distinct so an operator can
 * see that two sources are sending visually-similar tags.
 */
function FileTagsSection({ tags }: { tags: MediaTag[] }) {
  // Group preserving insertion order from the backend, which orders
  // by (source, name). Map preserves insertion order in modern JS.
  const grouped = new Map<string, MediaTag[]>();
  for (const t of tags) {
    if (!grouped.has(t.source)) grouped.set(t.source, []);
    grouped.get(t.source)!.push(t);
  }
  return (
    <div className="file-drawer-section">
      <div className="file-drawer-section-head">
        <span>Tags</span>
        <span className="text-muted text-[11.5px] font-normal">
          {tags.length}
        </span>
      </div>
      <div className="px-3 py-2 flex flex-col gap-2">
        {Array.from(grouped.entries()).map(([source, group]) => (
          <div key={source} className="flex flex-col gap-1">
            <div className="text-[10.5px] uppercase tracking-[0.06em] font-semibold text-muted-2">
              {tagSourceLabel(source)}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {group.map((t) => (
                <Tag key={t.id}>{t.name}</Tag>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Map source strings to human labels. Unknown sources surface a
 *  capitalized version of the raw key, so a future "trakt" source
 *  appears as "From trakt" instead of the bare token. */
function tagSourceLabel(source: string): string {
  switch (source) {
    case "manual":
      return "Manual";
    case "rule":
      return "From rules";
    case "sonarr":
      return "From Sonarr";
    case "radarr":
      return "From Radarr";
    case "bazarr":
      return "From Bazarr";
    default:
      return `From ${source.charAt(0).toUpperCase()}${source.slice(1)}`;
  }
}

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="file-meta-cell">
      <div className="text-[10.5px] uppercase tracking-[0.06em] font-semibold text-muted-2">
        {label}
      </div>
      <div className={cn("font-mono text-[12.5px] text-text mt-0.5 truncate")}>
        {value}
      </div>
    </div>
  );
}

function fmtDuration(sec: number): string {
  if (!isFinite(sec) || sec <= 0) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s
      .toString()
      .padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatActionsSummary(s: Record<string, unknown>): string {
  // The actions_summary blob is rule-engine internal — surface it in a
  // human-readable form rather than dumping raw JSON. Empty / unknown
  // shapes fall back to a thin "no actions recorded" line.
  if (!s || typeof s !== "object" || Object.keys(s).length === 0) {
    return "no recorded actions";
  }
  const bits: string[] = [];
  if (typeof s.severity === "string") bits.push(`severity=${s.severity}`);
  if (Array.isArray(s.add_tags) && s.add_tags.length > 0) {
    bits.push(`tags=${s.add_tags.join(",")}`);
  }
  if (Array.isArray(s.queue_optimizations) && s.queue_optimizations.length > 0) {
    bits.push(`queue=${s.queue_optimizations.join(",")}`);
  }
  return bits.length > 0 ? bits.join(" · ") : "no recorded actions";
}

function copyToClipboard(text: string): void {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(text);
  }
}
