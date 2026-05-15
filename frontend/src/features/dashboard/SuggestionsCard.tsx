/**
 * Stage 16: data-driven rule suggestions card on the Dashboard.
 *
 * Reads from ``GET /api/v1/rules/suggestions`` (pending only) and
 * renders one row per suggestion with:
 *   - heuristic label + suggestion name
 *   - 3-cell projection: files affected / est. runtime / confidence
 *   - 3 actions: Deploy / Review → / Dismiss
 *
 * Deploy: one-click deploy of the analyzer's verbatim definition.
 * Review →: opens the parent's review modal (`onReview` callback)
 *           which pre-fills the Stage 15 visual rule builder.
 * Dismiss: 30-day sticky dismiss (the backend enforces the window;
 *          this UI just calls the endpoint).
 *
 * When the user has no integrations or hasn't accumulated enough
 * playback events for the analyzer to fire (the analyzer floor is
 * 20 events in 30 days), the card renders a friendly empty state
 * rather than disappearing — the operator should see *why* there
 * are no suggestions, not silence.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  useDeploySuggestion,
  useDismissSuggestion,
  useRunAnalyzer,
  useRuleSuggestions,
  type RuleSuggestion,
} from "@/hooks/useRules";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { useUiStore } from "@/stores/uiStore";

const HEURISTIC_LABEL: Record<string, string> = {
  high_transcode_codec: "Transcode codec",
  bitrate_ceiling: "Bitrate ceiling",
  container_compat: "Container compat",
  resolution_mismatch: "Resolution",
  failed_playback: "Failed playback",
};

export function SuggestionsCard({
  onReview,
}: {
  /** Callback fired when the user clicks "Review →" on a row. The
      parent owns the modal so the same modal can also be opened from
      other places later if we want. */
  onReview: (suggestion: RuleSuggestion) => void;
}) {
  const suggestions = useRuleSuggestions();
  const runAnalyzer = useRunAnalyzer();

  // Stage 11 audit fix (Issue 16): per-section collapse state.
  // The actions slot already carries the "Run now" button; we
  // sit the chevron beside it on the right edge. Run-now stays
  // active even when collapsed — the operator can re-run the
  // analyzer without expanding the card.
  const hidden = useUiStore((s) => s.dashboardHidden.includes("suggestions"));
  const toggle = useUiStore((s) => s.toggleDashboardSection);

  // Show at most 5 by default; the user can expand to see the rest.
  // Anything more than ~8 starts crowding the dashboard.
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? (suggestions.data ?? []) : (suggestions.data ?? []).slice(0, 5);
  const hiddenCount = (suggestions.data?.length ?? 0) - visible.length;

  return (
    <Card>
      <CardHead
        title="Rule suggestions"
        subtitle={
          suggestions.data && suggestions.data.length > 0
            ? `${fmtNum(suggestions.data.length)} pending`
            : "Data-driven recommendations from Plex/Jellyfin playback"
        }
        actions={
          <>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => runAnalyzer.mutate()}
              disabled={runAnalyzer.isPending}
              title="Re-run the analyzer now"
            >
              <Icon
                name="refresh"
                size={11}
                className={cn(runAnalyzer.isPending && "animate-spin")}
              />
              <span className="ml-1">{runAnalyzer.isPending ? "Analyzing…" : "Run now"}</span>
            </Button>
            <button
              type="button"
              onClick={() => toggle("suggestions")}
              className="shrink-0 text-muted-2 hover:text-text"
              aria-label={hidden ? "Expand Rule suggestions" : "Collapse Rule suggestions"}
              aria-expanded={!hidden}
              title={hidden ? "Expand" : "Collapse"}
            >
              <Icon name={hidden ? "chev_right" : "chev_down"} size={14} />
            </button>
          </>
        }
      />
      {!hidden ? (
        <CardBodyFlush>
          {suggestions.isLoading ? (
            <div className="px-4 py-6">
              <LoadingState label="Loading suggestions…" />
            </div>
          ) : suggestions.isError ? (
            <div className="px-4 py-6">
              <ErrorState
                title="Couldn't load suggestions"
                description={(suggestions.error as Error)?.message}
              />
            </div>
          ) : !suggestions.data || suggestions.data.length === 0 ? (
            <div className="px-4 py-6">
              <EmptyState
                icon="rules"
                title="No suggestions yet"
                description={
                  runAnalyzer.data?.skipped_too_few_events
                    ? `Auditarr saw ${fmtNum(runAnalyzer.data.examined_events)} playback event${runAnalyzer.data.examined_events === 1 ? "" : "s"} in the last 30 days — the analyzer needs at least 20 before it surfaces suggestions. Connect Plex or Jellyfin and let some playback accumulate.`
                    : "The analyzer surfaces recurring playback issues (transcodes, bitrate ceilings, failed playbacks) as Auditarr rule suggestions. Connect Plex or Jellyfin and let some playback accumulate, then come back."
                }
              />
            </div>
          ) : (
            <>
              {visible.map((s) => (
                <SuggestionRow key={s.id} suggestion={s} onReview={() => onReview(s)} />
              ))}
              {hiddenCount > 0 ? (
                <button
                  onClick={() => setExpanded(true)}
                  className="block w-full px-4 py-2 text-[12px] text-muted-2 hover:text-text border-t border-border bg-surface-2"
                >
                  Show {hiddenCount} more
                </button>
              ) : expanded && (suggestions.data?.length ?? 0) > 5 ? (
                <button
                  onClick={() => setExpanded(false)}
                  className="block w-full px-4 py-2 text-[12px] text-muted-2 hover:text-text border-t border-border bg-surface-2"
                >
                  Show fewer
                </button>
              ) : null}
            </>
          )}
        </CardBodyFlush>
      ) : null}
    </Card>
  );
}

// ── One row ─────────────────────────────────────────────────
function SuggestionRow({
  suggestion,
  onReview,
}: {
  suggestion: RuleSuggestion;
  onReview: () => void;
}) {
  const deploy = useDeploySuggestion();
  const dismiss = useDismissSuggestion();
  const isPending = deploy.isPending || dismiss.isPending;

  const confidencePct = Math.round(suggestion.confidence * 100);
  const confidenceTone =
    confidencePct >= 80 ? "text-sev-ok" : confidencePct >= 50 ? "text-sev-info" : "text-muted-2";

  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0 flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <Pill className="text-[10px] text-muted-2 border-border bg-surface-2">
            {HEURISTIC_LABEL[suggestion.heuristic] ?? suggestion.heuristic}
          </Pill>
          <button
            onClick={onReview}
            className="text-[13px] font-medium truncate hover:underline text-left"
            title="Review this suggestion"
          >
            {suggestion.name}
          </button>
        </div>
        {/* 3-cell projection row */}
        <div className="mt-1.5 flex items-center gap-4 text-[11px]">
          <div>
            <span className="text-muted-2">Files affected</span>{" "}
            <span className="font-mono text-text">{fmtNum(suggestion.files_affected)}</span>
          </div>
          {suggestion.est_runtime_s != null ? (
            <div>
              <span className="text-muted-2">Est. runtime</span>{" "}
              <span className="font-mono text-text">{fmtRuntime(suggestion.est_runtime_s)}</span>
            </div>
          ) : null}
          <div>
            <span className="text-muted-2">Confidence</span>{" "}
            <span className={cn("font-mono", confidenceTone)}>{confidencePct}%</span>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button
          size="sm"
          variant="primary"
          disabled={isPending}
          onClick={() => deploy.mutate({ id: suggestion.id, patch: {} })}
          title="Deploy this suggestion as a rule, verbatim"
        >
          <Icon name="check" size={11} />
          <span className="ml-1">Deploy</span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onReview}
          title="Review and tweak before deploying"
        >
          Review →
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={isPending}
          onClick={() => {
            if (
              confirm(
                `Dismiss "${suggestion.name}"? Auditarr won't suggest this pattern again for 30 days.`,
              )
            ) {
              dismiss.mutate({ id: suggestion.id });
            }
          }}
          title="Dismiss (sticky for 30 days)"
        >
          <Icon name="x" size={11} />
        </Button>
      </div>
    </div>
  );
}

function fmtRuntime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem === 0 ? `${hours}h` : `${hours}h ${rem}m`;
}
