/**
 * Stage 5 — Optimization profile row.
 *
 * Extracted from the inline ``ProfileRow`` at the top of
 * ``OptimizationPage.tsx``. Renders one row of the Profiles list:
 * name + status tags + edit/toggle/delete actions.
 *
 * The settings object is destructured loosely because the profile
 * schema is operator-defined (stored as JSON); only the
 * commonly-displayed keys (video.codec, video.crf, output.container)
 * are read out for the inline summary.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import type { OptimizationProfile } from "@/hooks/useOptimization";

export interface OptimizationProfileRowProps {
  profile: OptimizationProfile;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}

export function OptimizationProfileRow({
  profile,
  onEdit,
  onToggle,
  onDelete,
}: OptimizationProfileRowProps) {
  const settings = profile.settings as {
    video?: { codec?: string; crf?: number };
    output?: { container?: string };
  };
  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0 flex items-center gap-3">
      <Icon name="optimize" size={14} className="text-muted-2 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <button
            className="text-[13px] font-medium truncate hover:underline text-left"
            onClick={onEdit}
          >
            {profile.name}
          </button>
          {/* Stage 8 audit fix (Issue 10): always-present state pill
              replaces the conditional "disabled" pill. State is
              visible on every row regardless of value. */}
          {profile.enabled ? (
            <Pill sev="ok">Active</Pill>
          ) : (
            <Pill>Paused</Pill>
          )}
          {settings.video?.codec ? <Tag>{String(settings.video.codec)}</Tag> : null}
          {settings.output?.container ? (
            <Tag>.{String(settings.output.container)}</Tag>
          ) : null}
          {settings.video?.crf !== undefined ? (
            <Tag>CRF {String(settings.video.crf)}</Tag>
          ) : null}
        </div>
        {profile.description ? (
          <div className="text-[11.5px] text-muted-2 mt-0.5 truncate">
            {profile.description}
          </div>
        ) : null}
      </div>
      <Button size="sm" variant="ghost" onClick={onEdit} title="Edit">
        <Icon name="edit" size={12} />
      </Button>
      {/* Stage 8 audit fix (Issue 10): text-labeled toggle. "Pause"
          on an active profile, "Activate" on a paused one. Replaces
          the ambiguous check/x icon toggle. */}
      <Button
        size="sm"
        variant="ghost"
        onClick={onToggle}
        title={profile.enabled ? "Pause this profile" : "Activate this profile"}
        aria-label={profile.enabled ? "Pause profile" : "Activate profile"}
      >
        {profile.enabled ? "Pause" : "Activate"}
      </Button>
      <Button size="sm" variant="ghost" onClick={onDelete} title="Delete">
        <Icon name="trash" size={12} />
      </Button>
    </div>
  );
}
