/**
 * Stage 6 — "Add a channel" directory card.
 *
 * Extracted from the inline directory section. Renders each
 * available notification kind as a row with a small Add button that
 * opens the create dialog for that kind.
 */

import { Button } from "@/components/ui/Button";
import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import {
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { NotificationKind, useNotificationKinds } from "@/hooks/useNotifications";

export interface NotificationKindsCardProps {
  kinds: ReturnType<typeof useNotificationKinds>;
  onPick: (kind: NotificationKind) => void;
}

export function NotificationKindsCard({
  kinds,
  onPick,
}: NotificationKindsCardProps) {
  return (
    <Card>
      <CardHead title="Add a channel" />
      <CardBodyFlush>
        {kinds.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading channel types…" />
          </div>
        ) : kinds.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load channel kinds"
              description={(kinds.error as Error)?.message}
            />
          </div>
        ) : (
          kinds.data?.map((kind) => (
            <div
              key={kind.kind}
              className="px-4 py-2.5 border-b border-border last:border-b-0 flex items-center gap-3"
            >
              <Icon
                name="notifications"
                size={14}
                className="text-muted-2"
              />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium">{kind.label}</div>
                <div className="text-[11.5px] text-muted-2">
                  kind: {kind.kind}
                  {kind.secret_fields.length
                    ? ` · secrets: ${kind.secret_fields.join(", ")}`
                    : ""}
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onPick(kind)}
              >
                <Icon name="plus" size={12} />
                <span className="ml-1">Add</span>
              </Button>
            </div>
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
