/**
 * Stage 6 — Notification delivery log row.
 *
 * Extracted from the inline ``DeliveryRow``. Read-only entry —
 * deliveries are an append-only log of past attempts.
 */

import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import type { NotificationDelivery } from "@/hooks/useNotifications";

import { formatDuration, statusClass } from "./notificationsShared";

export interface NotificationDeliveryRowProps {
  delivery: NotificationDelivery;
}

export function NotificationDeliveryRow({
  delivery,
}: NotificationDeliveryRowProps) {
  return (
    <div className="px-4 py-2 border-b border-border last:border-b-0 flex items-center gap-3">
      <Icon
        name="notifications"
        size={13}
        className="text-muted-2 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[12.5px] font-medium truncate">
            {delivery.channel_name}
          </span>
          <Tag>{delivery.channel_kind}</Tag>
          <Tag>{delivery.severity}</Tag>
        </div>
        <div className="text-[11px] text-muted-2 mt-0.5 truncate">
          {new Date(delivery.attempted_at).toLocaleString()}
          {delivery.duration_ms !== null
            ? ` · ${formatDuration(delivery.duration_ms)}`
            : ""}
          {delivery.error ? ` · ${delivery.error}` : ""}
        </div>
      </div>
      <Pill className={statusClass(delivery.status)}>{delivery.status}</Pill>
    </div>
  );
}
