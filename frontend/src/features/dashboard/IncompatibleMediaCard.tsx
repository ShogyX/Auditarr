/**
 * v1.9 Stage 9.5.7 (OP-9) — Rule-flagged incompatible-media tile.
 *
 * Surfaces the count of media files carrying at least one tag
 * whose name matches the ``*incompatible*`` convention. Built-in
 * rules use ``plex-incompatible-video`` / ``plex-incompatible-audio`` /
 * ``jellyfin-incompatible-video``; operator-authored rules with
 * their own ``*-incompatible-*`` tags surface here automatically.
 *
 * Empty state (count == 0) hides the tile so a clean library
 * stays uncluttered. Operators who haven't yet enabled any
 * incompatibility-tagging rule see nothing — the surface is
 * activated by the rules they configure.
 *
 * Click-through: "View files" links to /files?tag=incompatible
 * (substring filter on tag name, supported by the Files page's
 * tag filter on tag-name LIKE).
 */
import { Link } from "react-router-dom";

import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { useDashboardIncompatibleMedia } from "@/hooks/useDashboard";
import { fmtNum } from "@/lib/format";

export function IncompatibleMediaCard() {
  const query = useDashboardIncompatibleMedia();

  if (query.isLoading) return null;

  const data = query.data;
  if (!data || data.count === 0) return null;

  return (
    <Card data-testid="incompatible-media-card">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-[13px] font-semibold flex items-center gap-1.5">
          <Icon name="alert" size={12} className="text-sev-error" />
          Rule-flagged incompatibilities
        </h3>
        <p className="text-[11.5px] text-muted-2 mt-0.5">
          Media a rule tagged as having an audio or video codec your
          target player can't direct-play.
        </p>
      </div>
      <div className="px-4 py-3 flex items-baseline justify-between gap-3">
        <div className="text-[20px] font-semibold text-sev-error">
          {fmtNum(data.count)}
          <span className="ml-1 text-[12px] font-normal text-muted-2">
            file{data.count === 1 ? "" : "s"}
          </span>
        </div>
        <Link
          to="/files?tag=incompatible"
          className="text-[12px] text-accent hover:underline whitespace-nowrap"
          data-testid="incompatible-media-view-link"
        >
          View files →
        </Link>
      </div>
    </Card>
  );
}
