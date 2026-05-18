/**
 * v1.9 Stage 9.5.7 (OP-8) — Foreign-audio-without-subs tile.
 *
 * Surfaces the count of media files whose primary audio language
 * is NOT in the operator's preferred-audio list AND that carry no
 * subtitle track in any of the preferred-subtitle languages.
 *
 * Empty state (count == 0) hides the tile so it doesn't shout at
 * a clean library. When the preferences are unconfigured (empty
 * lists), the tile renders with a config nudge pointing at
 * Settings → Workspace → Language preferences.
 *
 * Click-through: "View files" links to /files?tag=foreign-audio-no-subs
 * so the operator can drill straight from the count to the
 * matching rows. The tag is applied by the optional built-in
 * rule that the operator can enable; the link is best-effort and
 * shows the empty filter if no rule has tagged anything yet.
 */
import { Link } from "react-router-dom";

import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { useDashboardForeignAudio } from "@/hooks/useDashboard";
import { fmtNum } from "@/lib/format";

export function ForeignAudioCard() {
  const query = useDashboardForeignAudio();

  if (query.isLoading) {
    return null;
  }

  const data = query.data;
  if (!data) return null;

  const unconfigured =
    data.preferred_audio_languages.length === 0 &&
    data.preferred_subtitle_languages.length === 0;

  // Hide the tile entirely when there's nothing to say — no
  // matches AND the operator has configured preferences. An
  // unconfigured operator still sees the tile (with the nudge)
  // so they discover the feature; a configured operator with
  // zero matches sees nothing (their library is clean).
  if (data.count === 0 && !unconfigured) {
    return null;
  }

  return (
    <Card data-testid="foreign-audio-card">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-[13px] font-semibold flex items-center gap-1.5">
          <Icon name="info" size={12} className="text-sev-warn" />
          Foreign audio without preferred subtitles
        </h3>
        <p className="text-[11.5px] text-muted-2 mt-0.5">
          Media whose primary audio isn't in your preferred languages
          and that carries no subtitles in your preferred set.
        </p>
      </div>
      <div className="px-4 py-3">
        {unconfigured ? (
          <p className="text-[12.5px] text-muted-2">
            No preferred languages configured yet.{" "}
            <Link
              to="/settings"
              className="text-accent hover:underline"
              data-testid="foreign-audio-configure-link"
            >
              Set preferred audio + subtitle languages
            </Link>{" "}
            to enable this surface.
          </p>
        ) : (
          <div className="flex items-baseline justify-between gap-3">
            <div className="text-[20px] font-semibold text-sev-warn">
              {fmtNum(data.count)}
              <span className="ml-1 text-[12px] font-normal text-muted-2">
                file{data.count === 1 ? "" : "s"}
              </span>
            </div>
            <Link
              to="/files?tag=foreign-audio-no-subs"
              className="text-[12px] text-accent hover:underline whitespace-nowrap"
              data-testid="foreign-audio-view-link"
            >
              View files →
            </Link>
          </div>
        )}
        {!unconfigured ? (
          <div className="mt-2 text-[11px] text-muted-2">
            Preferred audio:{" "}
            <code className="font-mono">
              {data.preferred_audio_languages.join(", ") || "(none)"}
            </code>
            {" · "}
            Preferred subs:{" "}
            <code className="font-mono">
              {data.preferred_subtitle_languages.join(", ") || "(none)"}
            </code>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
