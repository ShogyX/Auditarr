/**
 * Stage 6 — Configured integrations card.
 *
 * Extracted from the inline ``ConfiguredCard`` in
 * ``IntegrationsPage``. Standard 4-way state branch (loading /
 * error / empty / data); each row is an ``IntegrationRow`` with its
 * own expandable discovery panel.
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { type useIntegrations, type Integration } from "@/hooks/useIntegrations";

import { IntegrationRow } from "./IntegrationRow";

export interface ConfiguredIntegrationsCardProps {
  integrations: ReturnType<typeof useIntegrations>;
  onCheck: (id: string) => void;
  /** Stage 9 audit fix (Issue 13): open the edit dialog for this row. */
  onEdit: (i: Integration) => void;
  onToggle: (i: Integration) => void;
  onDelete: (i: Integration) => void;
}

export function ConfiguredIntegrationsCard({
  integrations,
  onCheck,
  onEdit,
  onToggle,
  onDelete,
}: ConfiguredIntegrationsCardProps) {
  return (
    <Card>
      <CardHead
        title="Configured integrations"
        subtitle={
          integrations.data
            ? `${integrations.data.length} configured`
            : undefined
        }
      />
      <CardBodyFlush>
        {integrations.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : integrations.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load integrations"
              description={(integrations.error as Error)?.message}
            />
          </div>
        ) : !integrations.data || integrations.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="integrations"
              title="No integrations configured"
              description="Pick a connector above to get started."
            />
          </div>
        ) : (
          integrations.data.map((i) => (
            <IntegrationRow
              key={i.id}
              integration={i}
              onCheck={() => onCheck(i.id)}
              onEdit={() => onEdit(i)}
              onToggle={() => onToggle(i)}
              onDelete={() => onDelete(i)}
            />
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
