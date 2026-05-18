/**
 * v1.9 Stage 9.1 — Devices observed card.
 *
 * Renders the top devices ranked by total playback_count, with
 * a transcode-ratio bar per device so the operator sees which
 * clients are working hardest. Data source:
 * ``GET /api/v1/playback/devices``.
 *
 * The card answers questions like:
 *   - Which clients keep transcoding?
 *   - Which clients direct-play everything?
 *
 * Empty state — when no devices have been observed yet (fresh
 * install or no Plex/Jellyfin polled) — the card hides itself
 * rather than rendering an empty rectangle. The dashboard
 * grid uses CSS gap so a hidden card doesn't leave a hole.
 */

import { useQuery } from "@tanstack/react-query";

import { Card } from "@/components/ui/Card";
import { apiClient } from "@/services/apiClient";

interface PlaybackDevice {
  id: string;
  integration_id: string;
  client_key: string;
  name: string | null;
  platform: string | null;
  product: string | null;
  device_model: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  playback_count: number;
  transcode_count: number;
  direct_play_count: number;
  direct_stream_count: number;
}

interface DevicesResponse {
  devices: PlaybackDevice[];
  total: number;
}

const TOP_N = 10;

export function DevicesCard() {
  const query = useQuery({
    queryKey: ["playback", "devices", { limit: TOP_N }],
    queryFn: () =>
      apiClient.get<DevicesResponse>(
        `/playback/devices?limit=${TOP_N}`,
      ),
    staleTime: 30_000,
  });

  if (query.isLoading) {
    return (
      <Card data-testid="devices-card">
        <div className="px-4 py-3 text-[12.5px] text-muted-2">
          Loading devices…
        </div>
      </Card>
    );
  }

  const devices = query.data?.devices ?? [];
  if (devices.length === 0) {
    // Hide the card entirely on empty state. See module comment.
    return null;
  }

  return (
    <Card data-testid="devices-card">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-[13px] font-semibold">Devices observed</h3>
        <p className="text-[11.5px] text-muted-2 mt-0.5">
          Top {TOP_N} by total playbacks. Transcode ratio shows how
          often the client needed re-encoding.
        </p>
      </div>
      <ul className="list-none p-0 m-0">
        {devices.map((d) => (
          <DeviceRow key={d.id} device={d} />
        ))}
      </ul>
    </Card>
  );
}

function DeviceRow({ device }: { device: PlaybackDevice }) {
  const total = device.playback_count || 0;
  const transcodeRatio =
    total > 0 ? device.transcode_count / total : 0;
  const transcodePct = Math.round(transcodeRatio * 100);

  return (
    <li
      className="px-4 py-2 border-b border-border last:border-b-0"
      data-testid="devices-card-row"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] font-medium truncate">
          {device.name || "(unnamed device)"}
        </span>
        <span className="text-[11.5px] text-muted-2 whitespace-nowrap">
          {total} play{total === 1 ? "" : "s"}
        </span>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <span className="text-[11px] text-muted-2 w-14 shrink-0">
          {device.platform || "?"}
        </span>
        <div
          className="flex-1 h-1.5 rounded bg-surface-2 overflow-hidden"
          title={`${transcodePct}% transcoded`}
        >
          <div
            className="h-full bg-sev-warn"
            style={{ width: `${transcodePct}%` }}
            data-testid="devices-card-transcode-bar"
          />
        </div>
        <span className="text-[11px] text-muted-2 w-10 text-right">
          {transcodePct}%
        </span>
      </div>
    </li>
  );
}
