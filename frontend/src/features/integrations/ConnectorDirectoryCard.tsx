/**
 * Stage 6 — Available connectors directory card.
 *
 * Extracted from the inline ``ConnectorDirectory`` in
 * ``IntegrationsPage``. Renders the grid of "click to configure"
 * tiles for each available integration kind. Each tile shows the
 * label + count of config options + count of secret fields so the
 * operator knows roughly what they're getting into before clicking.
 */

import { Card, CardBody, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, LoadingState } from "@/components/ui/States";
import type { IntegrationKind } from "@/hooks/useIntegrations";
import { cn } from "@/lib/cn";

export interface ConnectorDirectoryCardProps {
  kinds: IntegrationKind[];
  isLoading: boolean;
  onPick: (k: IntegrationKind) => void;
}

export function ConnectorDirectoryCard({
  kinds,
  isLoading,
  onPick,
}: ConnectorDirectoryCardProps) {
  return (
    <Card>
      <CardHead
        title="Available connectors"
        subtitle="Click to configure a new integration"
      />
      <CardBody>
        {isLoading ? (
          <LoadingState label="Discovering connectors…" />
        ) : kinds.length === 0 ? (
          <EmptyState
            icon="integrations"
            title="No connectors loaded"
            description="Drop an integration plugin into /app/plugins/ and restart."
          />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {kinds.map((k) => (
              <button
                key={k.kind}
                onClick={() => onPick(k)}
                className={cn(
                  "text-left flex items-center gap-3 p-3 rounded-md border border-border",
                  "bg-surface-2 hover:bg-[var(--hover)] transition-colors",
                )}
              >
                <Icon name="integrations" size={18} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium">{k.label}</div>
                  <div className="text-[11.5px] text-muted truncate">
                    {Object.keys(k.config_schema.properties ?? {}).length}{" "}
                    options · {k.secret_fields.length} secret(s)
                  </div>
                </div>
                <Icon name="plus" size={14} className="text-muted-2" />
              </button>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
