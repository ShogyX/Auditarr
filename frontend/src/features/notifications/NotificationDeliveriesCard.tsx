/**
 * Stage 6 — Notification deliveries card.
 *
 * Read-only log of recent delivery attempts. Standard 4-way state
 * branch; each row is a ``NotificationDeliveryRow``.
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { useNotificationDeliveries } from "@/hooks/useNotifications";

import { NotificationDeliveryRow } from "./NotificationDeliveryRow";

export interface NotificationDeliveriesCardProps {
  deliveries: ReturnType<typeof useNotificationDeliveries>;
  pageSize?: number;
}

export function NotificationDeliveriesCard({
  deliveries,
  pageSize = 30,
}: NotificationDeliveriesCardProps) {
  return (
    <Card>
      <CardHead
        title="Recent deliveries"
        subtitle={
          deliveries.data
            ? `${deliveries.data.length} of last ${pageSize}`
            : undefined
        }
      />
      <CardBodyFlush>
        {deliveries.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : deliveries.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load deliveries"
              description={(deliveries.error as Error)?.message}
            />
          </div>
        ) : !deliveries.data || deliveries.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="notifications"
              title="No deliveries yet"
              description="Once a rule fires a notify action, deliveries appear here."
            />
          </div>
        ) : (
          deliveries.data.map((delivery) => (
            <NotificationDeliveryRow key={delivery.id} delivery={delivery} />
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
