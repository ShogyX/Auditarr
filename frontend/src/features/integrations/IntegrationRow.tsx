/**
 * Stage 6 — Configured integration row.
 *
 * Extracted from the inline ``IntegrationRow`` in
 * ``IntegrationsPage``. Two visual states: collapsed (one line with
 * name + kind + health + actions) and expanded (the discovery panel
 * below the row). The expand chevron is its own button to the left
 * of the name; clicking it does NOT trigger any other handler.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import type { Integration } from "@/hooks/useIntegrations";
import { useSyncTags } from "@/hooks/useIntegrations";
import { useCursors, useResetCursors } from "@/hooks/usePlayback";
import { useAuthStore } from "@/stores/authStore";
import { toast } from "@/lib/toast";

import { HealthPill } from "./HealthPill";
import { IntegrationDiscoverPanel } from "./IntegrationDiscoverPanel";

export interface IntegrationRowProps {
  integration: Integration;
  onCheck: () => void;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}

// Stage 12 (audit follow-up): integrations whose poller fills
// playback_events. Only these show the "last polled" line + reset
// button; other integration kinds (Sonarr, Radarr, Bazarr) don't
// have a polling cursor and the line would just be noise.
const POLLED_KINDS = new Set(["plex", "jellyfin"]);

// Stage 13 (audit follow-up): integrations that mirror tags from
// their upstream. Plex/Jellyfin don't expose a tag vocabulary
// usefully (the manager's ``sync_tags`` returns ``[]`` for them);
// hiding the button entirely keeps the UX clean for those rows
// instead of advertising a no-op affordance.
const TAG_SYNC_KINDS = new Set(["sonarr", "radarr", "bazarr"]);

/** Best-effort relative-time formatter. Falls back to a locale
 *  string for distances > 7 days. */
function fmtRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = now - then;
  if (diff < 0) return "just now";
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function IntegrationRow({
  integration,
  onCheck,
  onEdit,
  onToggle,
  onDelete,
}: IntegrationRowProps) {
  const [expanded, setExpanded] = useState(false);

  // Stage 12 (audit follow-up): cursor display + reset.
  // ``useCursors`` is global so the same query is shared across
  // every row — one network round-trip total, regardless of how
  // many integrations the page renders.
  const isPolled = POLLED_KINDS.has(integration.kind);
  const cursors = useCursors();
  const resetCursors = useResetCursors();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  // Stage 13 (audit follow-up): manual tag sync. Only enabled for
  // integration kinds whose manager actually exposes tag mirrors
  // (Sonarr/Radarr/Bazarr) AND only visible to admins. Endpoint is
  // admin-gated server-side; hiding the button avoids a needless
  // 403 round-trip when an operator clicks.
  const supportsTagSync = TAG_SYNC_KINDS.has(integration.kind);
  const syncTags = useSyncTags();

  // Find the most-recently-updated cursor for this integration —
  // there's typically one per (integration, cursor_kind) and Stage
  // 12 only writes "playback_events", but we show the most recent
  // either way so a future cursor_kind doesn't silently hide info.
  const cursorForIntegration = isPolled
    ? (cursors.data ?? [])
        .filter((c) => c.integration_id === integration.id)
        .sort(
          (a, b) =>
            new Date(b.updated_at).getTime() -
            new Date(a.updated_at).getTime(),
        )[0]
    : undefined;

  return (
    <>
      <div className="px-4 py-3 border-b border-border last:border-b-0 flex items-center gap-3">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 text-muted-2 hover:text-text"
        >
          <Icon name={expanded ? "chev_down" : "chev_right"} size={14} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium truncate">
              {integration.name}
            </span>
            <Tag>{integration.kind}</Tag>
            <HealthPill status={integration.health_status} />
            {/* Stage 8 audit fix (Issue 10): always-present state pill
                replaces the conditional "disabled" pill. HealthPill
                above shows the *connection* health (ok/degraded/error);
                this pill shows whether the operator has the integration
                enabled at all. The two are distinct — an enabled
                integration can be unhealthy, a disabled one is just
                paused. */}
            {integration.enabled ? (
              <Pill sev="ok">Active</Pill>
            ) : (
              <Pill>Paused</Pill>
            )}
          </div>
          {integration.health_detail ? (
            <div className="text-[11.5px] text-muted-2 mt-0.5 truncate">
              {integration.health_detail}
            </div>
          ) : null}
          {/* Stage 12 (audit follow-up): last-polled hint for Plex /
              Jellyfin. Hidden for integration kinds without a poll
              cursor. The reset link is admin-only — non-admins see
              just the timestamp. When there's no cursor yet (fresh
              install, hasn't polled), we say so explicitly rather
              than hiding the row. */}
          {isPolled ? (
            <div
              className="text-[11.5px] text-muted-2 mt-0.5 flex items-center gap-2"
              data-testid={`integration-cursor-${integration.id}`}
            >
              {cursorForIntegration ? (
                <span>
                  Last polled {fmtRelativeTime(cursorForIntegration.updated_at)}
                </span>
              ) : (
                <span>Not polled yet</span>
              )}
              {isAdmin && cursorForIntegration ? (
                <button
                  type="button"
                  onClick={() => {
                    if (
                      confirm(
                        `Reset poll cursor for "${integration.name}"? The next poll will re-walk from the start.`,
                      )
                    ) {
                      resetCursors.mutate(integration.id);
                    }
                  }}
                  disabled={resetCursors.isPending}
                  className="text-[11.5px] text-muted hover:text-text underline disabled:opacity-50"
                  aria-label={`Reset poll cursor for ${integration.name}`}
                  title="Force a full re-poll on the next tick"
                >
                  Reset cursor
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={onCheck}
          title="Run healthcheck"
        >
          <Icon name="refresh" size={12} />
        </Button>
        {/* Stage 13 (audit follow-up): manual tag sync. Hidden
            entirely when the integration kind doesn't expose tag
            mirrors AND when the user isn't an admin — both
            conditions need to be true to render. On success the
            toast shows the report counts so an operator can see
            what changed without leaving the page. */}
        {supportsTagSync && isAdmin ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={syncTags.isPending}
            onClick={() => {
              syncTags.mutate(integration.id, {
                onSuccess: (report) => {
                  toast(
                    `Tags synced — inserted ${report.inserted}, removed ${report.removed}` +
                      (report.skipped_no_path
                        ? ` (${report.skipped_no_path} skipped, no path)`
                        : ""),
                    "ok",
                  );
                },
                onError: (err) => {
                  toast(`Tag sync failed: ${(err as Error).message}`, "error");
                },
              });
            }}
            title="Mirror tags from this integration"
            aria-label={`Sync tags from ${integration.name}`}
          >
            {syncTags.isPending ? "Syncing…" : "Sync tags"}
          </Button>
        ) : null}
        {/* Stage 9 audit fix (Issue 13): edit affordance. Opens the
            connect dialog in edit mode (pre-filled name + config;
            secrets stay encrypted unless the operator re-enters
            them). Sits between healthcheck and the pause toggle —
            the same slot Optimization rows use for the edit pencil. */}
        <Button size="sm" variant="ghost" onClick={onEdit} title="Edit">
          <Icon name="edit" size={12} />
        </Button>
        {/* Stage 8 audit fix (Issue 10): text-labeled toggle. */}
        <Button
          size="sm"
          variant="ghost"
          onClick={onToggle}
          title={
            integration.enabled
              ? "Pause this integration"
              : "Activate this integration"
          }
          aria-label={
            integration.enabled ? "Pause integration" : "Activate integration"
          }
        >
          {integration.enabled ? "Pause" : "Activate"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDelete} title="Delete">
          <Icon name="trash" size={12} />
        </Button>
      </div>
      {expanded ? (
        <IntegrationDiscoverPanel integration={integration} />
      ) : null}
    </>
  );
}
