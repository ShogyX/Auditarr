/**
 * Stage 6 — Integrations page (slim orchestrator).
 *
 * Composes:
 *   - ``PageHeader``                      — title / subtitle
 *   - ``ConnectorDirectoryCard``          — pick a kind to add
 *   - ``ConfiguredIntegrationsCard``      — existing integrations
 *   - ``IntegrationConnectDialog``        — create (Stage 1 Modal)
 *
 * Stage 9 audit fix (Issue 13): the connect dialog now also serves
 * the edit flow. We keep two transient pieces of state:
 *   - ``connectingKind``    — set when the operator picked a new
 *                              connector kind from the directory
 *   - ``editingIntegration`` — set when the operator clicked Edit
 *                              on a configured row
 * Exactly one is set at a time; the dialog binds to whichever is
 * non-null. For the edit path, we resolve the matching IntegrationKind
 * from the kinds list so the dialog can re-use the schema-driven
 * inputs without re-querying.
 *
 * Pre-Stage-6:  556 LOC
 * Post-Stage-6: ~65 LOC (this file)
 */

import { useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { useHelpKey } from "@/hooks/useHelpKey";
import {
  useDeleteIntegration,
  useIntegrationKinds,
  useIntegrations,
  useTriggerHealthcheck,
  useUpdateIntegration,
  type Integration,
  type IntegrationKind,
} from "@/hooks/useIntegrations";

import { ConfiguredIntegrationsCard } from "./ConfiguredIntegrationsCard";
import { ConnectorDirectoryCard } from "./ConnectorDirectoryCard";
import { IntegrationConnectDialog } from "./IntegrationConnectDialog";

export function IntegrationsPage() {
  useHelpKey("integrations.overview");

  const integrations = useIntegrations();
  const kinds = useIntegrationKinds();
  const remove = useDeleteIntegration();
  const update = useUpdateIntegration();
  const healthcheck = useTriggerHealthcheck();
  const [connectingKind, setConnectingKind] = useState<IntegrationKind | null>(
    null,
  );
  const [editingIntegration, setEditingIntegration] =
    useState<Integration | null>(null);

  // Resolve the kind metadata for the integration the operator is
  // editing. Returns null if the kinds list hasn't loaded or the
  // integration's kind isn't recognized — in either case we don't
  // render the dialog (the row's Edit button stays inert until the
  // kinds query resolves, which is usually within milliseconds of
  // the page load thanks to React Query caching).
  const editingKind: IntegrationKind | null = editingIntegration
    ? (kinds.data ?? []).find((k) => k.kind === editingIntegration.kind) ?? null
    : null;

  return (
    <>
      <PageHeader
        title="Integrations"
        sub="Connect to Plex, Sonarr, Radarr, and other services"
        helpKey="integrations.overview"
      />
      <div className="p-6 flex flex-col gap-6 max-w-4xl">
        <ConnectorDirectoryCard
          kinds={kinds.data ?? []}
          isLoading={kinds.isLoading}
          onPick={setConnectingKind}
        />

        <ConfiguredIntegrationsCard
          integrations={integrations}
          onCheck={(id) => healthcheck.mutate(id)}
          onEdit={(integration) => setEditingIntegration(integration)}
          onToggle={(integration) =>
            update.mutate({
              id: integration.id,
              patch: { enabled: !integration.enabled },
            })
          }
          onDelete={(integration) => {
            if (confirm(`Delete integration "${integration.name}"?`)) {
              remove.mutate(integration.id);
            }
          }}
        />
      </div>

      {connectingKind ? (
        <IntegrationConnectDialog
          kind={connectingKind}
          onClose={() => setConnectingKind(null)}
        />
      ) : null}

      {editingIntegration && editingKind ? (
        <IntegrationConnectDialog
          kind={editingKind}
          integration={editingIntegration}
          onClose={() => setEditingIntegration(null)}
        />
      ) : null}
    </>
  );
}
