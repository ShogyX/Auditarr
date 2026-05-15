/**
 * Stage 6 — Configured notification channels card.
 *
 * Standard 4-way state branch; each row is a
 * ``NotificationChannelRow``.
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type {
  NotificationChannel,
  useNotificationChannels,
} from "@/hooks/useNotifications";

import { NotificationChannelRow } from "./NotificationChannelRow";

export interface NotificationChannelsCardProps {
  channels: ReturnType<typeof useNotificationChannels>;
  onToggle: (channel: NotificationChannel) => void;
  onTest: (channel: NotificationChannel) => void;
  onDelete: (channel: NotificationChannel) => void;
}

export function NotificationChannelsCard({
  channels,
  onToggle,
  onTest,
  onDelete,
}: NotificationChannelsCardProps) {
  return (
    <Card>
      <CardHead
        title="Channels"
        subtitle={
          channels.data ? `${channels.data.length} configured` : undefined
        }
      />
      <CardBodyFlush>
        {channels.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : channels.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load channels"
              description={(channels.error as Error)?.message}
            />
          </div>
        ) : !channels.data || channels.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="notifications"
              title="No channels configured"
              description="Add a channel above to start delivering rule alerts."
            />
          </div>
        ) : (
          channels.data.map((channel) => (
            <NotificationChannelRow
              key={channel.id}
              channel={channel}
              onToggle={() => onToggle(channel)}
              onTest={() => onTest(channel)}
              onDelete={() => onDelete(channel)}
            />
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
