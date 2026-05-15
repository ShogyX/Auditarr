/**
 * Stage 6 — Notification channel row.
 *
 * Extracted from the inline ``ChannelRow``. Three actions: test
 * (sends a dummy notification at info severity), toggle (enable /
 * disable), delete. The threshold label is computed from the
 * ``SEVERITY_RANK_OPTIONS`` table so the displayed text matches the
 * dropdown vocabulary in the create dialog.
 */

import { useMemo } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import type { NotificationChannel } from "@/hooks/useNotifications";

import { SEVERITY_RANK_OPTIONS, statusClass } from "./notificationsShared";

export interface NotificationChannelRowProps {
  channel: NotificationChannel;
  onToggle: () => void;
  onTest: () => void;
  onDelete: () => void;
}

export function NotificationChannelRow({
  channel,
  onToggle,
  onTest,
  onDelete,
}: NotificationChannelRowProps) {
  const thresholdLabel = useMemo(() => {
    const found = SEVERITY_RANK_OPTIONS.find(
      (o) => o.value === channel.min_severity_rank,
    );
    return found?.label ?? `rank ≥ ${channel.min_severity_rank}`;
  }, [channel.min_severity_rank]);

  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0 flex items-center gap-3">
      <Icon
        name="notifications"
        size={14}
        className="text-muted-2 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium truncate">
            {channel.name}
          </span>
          <Tag>{channel.kind}</Tag>
          {!channel.enabled ? <Pill>disabled</Pill> : null}
          {channel.last_delivery_status ? (
            <Pill className={statusClass(channel.last_delivery_status)}>
              {channel.last_delivery_status}
            </Pill>
          ) : null}
        </div>
        <div className="text-[11.5px] text-muted-2 mt-0.5 truncate">
          Threshold: {thresholdLabel}
          {channel.last_delivery_at
            ? ` · Last delivery ${new Date(channel.last_delivery_at).toLocaleString()}`
            : ""}
          {channel.last_delivery_error
            ? ` · ${channel.last_delivery_error}`
            : ""}
        </div>
      </div>
      <Button size="sm" variant="ghost" onClick={onTest} title="Send test">
        <Icon name="refresh" size={12} />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={onToggle}
        title={channel.enabled ? "Disable" : "Enable"}
      >
        <Icon name={channel.enabled ? "check" : "x"} size={12} />
      </Button>
      <Button size="sm" variant="ghost" onClick={onDelete} title="Delete">
        <Icon name="trash" size={12} />
      </Button>
    </div>
  );
}
