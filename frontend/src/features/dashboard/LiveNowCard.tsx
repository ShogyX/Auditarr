/**
 * Stage 09 (v1.7) — "Live now" dashboard tile.
 *
 * Lists in-progress playback sessions across all enabled
 * Plex/Jellyfin integrations. The :func:`useLivePlaybacks` hook
 * polls ``GET /playback/live`` every 15 seconds; this card
 * renders the result.
 *
 * Per addendum A.7, when any session's path doesn't match a
 * known MediaFile (``media_file_id === null``), the card shows
 * an inline hint pointing the operator at the Integrations
 * page's path-mappings panel — the same hint the
 * SuggestionsCard uses, kept consistent so operators see the
 * same diagnostic everywhere.
 *
 * Empty state: when no sessions are active and at least one
 * Plex/Jellyfin integration is enabled, the card shows a
 * friendly "Nothing playing right now" message rather than
 * disappearing. When NO integrations support live playback at
 * all, the card collapses (rather than showing a misleading
 * "nothing playing").
 */

import { Link } from "react-router-dom";

import { Card, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  useLivePlaybacks,
  type LivePlaybackSession,
} from "@/hooks/usePlayback";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { useUiStore } from "@/stores/uiStore";

const DECISION_LABEL: Record<string, string> = {
  direct_play: "Direct play",
  direct_stream: "Direct stream",
  transcode: "Transcode",
};

// Maps decision → :class:`Pill`'s ``sev`` key. ``ok`` is the
// "this is fine" tone (direct play, no work for the server),
// ``warn`` for transcodes (server doing CPU work), ``info``
// for the in-between "remuxing, but no re-encode" case.
const DECISION_SEV: Record<string, string> = {
  direct_play: "ok",
  direct_stream: "info",
  transcode: "warn",
};

function fmtElapsed(startedIso: string): string {
  const start = new Date(startedIso).getTime();
  const now = Date.now();
  const secs = Math.max(0, Math.floor((now - start) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m ago`;
}

export function LiveNowCard() {
  const live = useLivePlaybacks();

  // Stage 11 audit pattern: respect the operator's per-section
  // collapse preference. Same uiStore key approach as the
  // other dashboard cards.
  const hidden = useUiStore((s) => s.dashboardHidden.includes("live_now"));
  // Stage 13 (plan §606) — when the operator moves this
  // card to the disabled rail, skip the whole render.
  const disabled = useUiStore((s) => s.dashboardDisabled.includes("live_now"));
  const toggle = useUiStore((s) => s.toggleDashboardSection);

  const sessions = live.data?.sessions ?? [];
  const total = live.data?.total ?? 0;
  const unresolved = live.data?.unresolved ?? 0;

  // Early-return AFTER all hooks (react-hooks/rules-of-hooks
  // requires the hook count to be stable across renders).
  if (disabled) return null;

  return (
    <Card>
      <CardHead
        title="Live now"
        subtitle={
          total === 0
            ? "Currently-playing sessions across Plex and Jellyfin"
            : `${fmtNum(total)} session${total === 1 ? "" : "s"} playing now`
        }
        actions={
          <button
            type="button"
            onClick={() => toggle("live_now")}
            className="text-[12px] text-muted hover:text-text"
            aria-label={hidden ? "Expand Live now" : "Collapse Live now"}
          >
            <Icon name={hidden ? "chev_down" : "chev_up"} size={14} />
          </button>
        }
      />
      {hidden ? null : live.isLoading ? (
        <div className="px-4 py-6">
          <LoadingState label="Polling live sessions…" />
        </div>
      ) : live.isError ? (
        <div className="px-4 py-6">
          <ErrorState
            title="Couldn't load live sessions"
            description={(live.error as Error)?.message}
          />
        </div>
      ) : sessions.length === 0 ? (
        <div className="px-4 py-6">
          <EmptyState
            icon="play"
            title="Nothing playing right now"
            description="When someone starts a Plex or Jellyfin session you'll see it here within 15 seconds."
          />
        </div>
      ) : (
        <div data-testid="live-now-sessions">
          {sessions.map((s) => (
            <LiveSessionRow key={`${s.integration_id}:${s.upstream_id}`} session={s} />
          ))}
          {unresolved > 0 ? (
            <div
              className="mx-4 my-3 rounded-md border border-border bg-surface-sunk px-3 py-2 text-[12px] text-muted"
              data-testid="live-now-unresolved-hint"
            >
              {fmtNum(unresolved)} of {fmtNum(total)} session
              {total === 1 ? "" : "s"} couldn't be matched to library
              files.{" "}
              <Link
                to="/integrations"
                className="text-accent hover:underline"
              >
                Configure path mappings
              </Link>
              .
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

function LiveSessionRow({ session }: { session: LivePlaybackSession }) {
  const decisionLabel = DECISION_LABEL[session.decision] ?? session.decision;
  const decisionSev = DECISION_SEV[session.decision] ?? "info";
  const paused = session.state === "paused";

  // Compose the "where + who" line. Some Jellyfin clients don't
  // report a username; some Plex sessions have no device name.
  // Pick the most operator-informative subset that's available.
  const meta = [
    session.user,
    session.device_name || session.device_kind,
    session.integration_name,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className="flex items-center gap-3 border-t border-border first:border-t-0 px-4 py-3"
      data-testid="live-now-session-row"
    >
      <Icon
        name={paused ? "pause" : "play"}
        size={16}
        className={cn(
          "shrink-0",
          paused ? "text-muted-2" : "text-accent",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          {session.media_file_id ? (
            <Link
              to={`/files/${session.media_file_id}`}
              className="truncate text-[13px] font-medium text-text hover:underline"
            >
              {session.title ?? session.source_path}
            </Link>
          ) : (
            <span className="truncate text-[13px] font-medium text-text">
              {session.title ?? session.source_path}
            </span>
          )}
          {paused ? (
            <span className="text-[11px] text-muted-2">paused</span>
          ) : null}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-muted">{meta}</div>
        {session.progress_pct !== null ? (
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-surface-sunk">
            <div
              className="h-full bg-accent"
              style={{
                width: `${Math.max(0, Math.min(100, session.progress_pct))}%`,
              }}
              role="progressbar"
              aria-valuenow={session.progress_pct}
              aria-valuemin={0}
              aria-valuemax={100}
            />
          </div>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <Pill sev={decisionSev}>{decisionLabel}</Pill>
        <span className="text-[11px] text-muted-2">
          {fmtElapsed(session.started_at)}
        </span>
      </div>
    </div>
  );
}
