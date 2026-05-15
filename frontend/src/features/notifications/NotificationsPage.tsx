/**
 * Stage 6 — Notifications page (slim orchestrator).
 *
 * Composes:
 *   - ``PageHeader``                       — title / subtitle
 *   - ``NotificationKindsCard``            — "add a channel" directory
 *   - ``NotificationChannelsCard``         — configured channels
 *   - ``NotificationDeliveriesCard``       — delivery log
 *   - ``NotificationChannelDialog``        — create (Stage 1 Modal)
 *
 * Pre-Stage-6:  497 LOC
 * Post-Stage-6: ~80 LOC (this file)
 */

import { useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { useHelpKey } from "@/hooks/useHelpKey";
import {
  useDeleteChannel,
  useNotificationChannels,
  useNotificationDeliveries,
  useNotificationKinds,
  useTestChannel,
  useUpdateChannel,
  type NotificationKind,
} from "@/hooks/useNotifications";

import { NotificationChannelDialog } from "./NotificationChannelDialog";
import { NotificationChannelsCard } from "./NotificationChannelsCard";
import { NotificationDeliveriesCard } from "./NotificationDeliveriesCard";
import { NotificationKindsCard } from "./NotificationKindsCard";

export function NotificationsPage() {
  useHelpKey("notifications.overview");

  const kinds = useNotificationKinds();
  const channels = useNotificationChannels();
  const deliveries = useNotificationDeliveries({ limit: 30 });
  const remove = useDeleteChannel();
  const update = useUpdateChannel();
  const test = useTestChannel();
  const [creatingKind, setCreatingKind] = useState<NotificationKind | null>(
    null,
  );

  return (
    <>
      <PageHeader
        title="Notifications"
        sub="Channels, delivery log, and on-demand testing"
        helpKey="notifications.overview"
      />
      <div className="p-6 flex flex-col gap-6 max-w-5xl">
        <NotificationKindsCard kinds={kinds} onPick={setCreatingKind} />

        <NotificationChannelsCard
          channels={channels}
          onToggle={(channel) =>
            update.mutate({
              id: channel.id,
              patch: { enabled: !channel.enabled },
            })
          }
          onTest={(channel) =>
            test.mutate({ id: channel.id, severity: "info" })
          }
          onDelete={(channel) => {
            if (confirm(`Delete channel "${channel.name}"?`)) {
              remove.mutate(channel.id);
            }
          }}
        />

        <NotificationDeliveriesCard deliveries={deliveries} pageSize={30} />
      </div>

      {creatingKind ? (
        <NotificationChannelDialog
          kind={creatingKind}
          onClose={() => setCreatingKind(null)}
        />
      ) : null}
    </>
  );
}
