/**
 * Stage 6 — Integration connect dialog.
 *
 * Adopts the Stage 1 ``Modal`` primitive. Replaces the hand-rolled
 * ``fixed inset-0`` overlay + manual Escape handler that the
 * pre-Stage-6 dialog used.
 *
 * Three button-state actions:
 *   - Cancel  → close without saving
 *   - Test    → call the upstream's healthcheck without persisting;
 *               render an inline pill with the result so the operator
 *               can iterate on credentials
 *   - Connect → persist the integration (and its secrets, server-
 *               side-encrypted)
 *
 * Stage 9 audit fix (Issue 13): the dialog now also handles edits.
 * When an ``integration`` prop is passed alongside ``kind``, the
 * dialog opens pre-populated with the existing name + config, the
 * primary CTA reads "Save" and PATCHes /integrations/{id} instead
 * of POSTing /integrations.
 *
 * Secrets are handled asymmetrically between create and edit:
 *   - Create: every secret field is required (the integration
 *     can't function without credentials).
 *   - Edit: secret fields default to empty and are optional. The
 *     placeholder spells out "leave blank to keep existing". The
 *     PATCH body only includes secret keys the operator actually
 *     re-entered, so the encrypted values on disk are preserved.
 *     The backend's IntegrationUpdatePayload treats secrets as a
 *     partial dict, so this is a clean contract match.
 */

import { useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
} from "@/components/ui/Modal";
import {
  useCreateIntegration,
  useGenerateWebhookSecret,
  useTestIntegration,
  useUpdateIntegration,
  type Integration,
  type IntegrationHealth,
  type IntegrationKind,
  type IntegrationUpdatePayload,
  type WebhookSecretResponse,
} from "@/hooks/useIntegrations";
import { cn } from "@/lib/cn";
import { toast } from "@/lib/toast";

import { IntegrationDynamicInput } from "./IntegrationDynamicInput";
import { initialConfig } from "./integrationsShared";

export interface IntegrationConnectDialogProps {
  kind: IntegrationKind;
  /**
   * When present, the dialog opens in edit mode: name + config are
   * pre-filled from this integration and the primary CTA PATCHes
   * /integrations/{id} instead of creating a new one.
   */
  integration?: Integration;
  onClose: () => void;
}

export function IntegrationConnectDialog({
  kind,
  integration,
  onClose,
}: IntegrationConnectDialogProps) {
  const isEdit = !!integration;
  const create = useCreateIntegration();
  const update = useUpdateIntegration();
  const test = useTestIntegration();

  // Pre-fill from the existing integration when editing. We merge
  // ``initialConfig`` underneath so any schema field that's missing
  // from a stored config (e.g. added in a newer release) still
  // renders with its default rather than as undefined.
  const [name, setName] = useState(
    integration ? integration.name : `${kind.label}`,
  );
  const [config, setConfig] = useState<Record<string, unknown>>(() =>
    integration
      ? { ...initialConfig(kind), ...integration.config }
      : initialConfig(kind),
  );
  const [secrets, setSecrets] = useState<Record<string, string>>(() =>
    Object.fromEntries(kind.secret_fields.map((s) => [s, ""])),
  );
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<IntegrationHealth | null>(null);

  const properties = useMemo(
    () => Object.entries(kind.config_schema.properties ?? {}),
    [kind],
  );

  // Only forward secret keys the operator actually filled in. Empty
  // values would otherwise overwrite encrypted secrets on disk
  // with empty strings — exactly the wrong behavior on edit.
  function nonEmptySecrets(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(secrets)) {
      if (v !== "") out[k] = v;
    }
    return out;
  }

  async function onTest() {
    setError(null);
    setTestResult(null);
    try {
      const result = await test.mutateAsync({
        name,
        kind: kind.kind,
        config,
        // Test always uses live values. In edit mode the operator
        // must (re-)enter secrets to probe the upstream — the
        // server doesn't have a "test with the stored secrets"
        // endpoint and surfacing that gap is more honest than
        // silently testing with empty creds.
        secrets,
      });
      setTestResult(result);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      if (isEdit && integration) {
        const patch: IntegrationUpdatePayload = {
          name,
          config,
        };
        const live = nonEmptySecrets();
        if (Object.keys(live).length > 0) patch.secrets = live;
        await update.mutateAsync({ id: integration.id, patch });
      } else {
        await create.mutateAsync({
          name,
          kind: kind.kind,
          config,
          secrets,
        });
      }
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const submitPending = isEdit ? update.isPending : create.isPending;

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      ariaLabel={isEdit ? `Edit ${integration!.name}` : `Connect ${kind.label}`}
      size="md"
    >
      <ModalHead
        title={isEdit ? `Edit ${integration!.name}` : `Connect ${kind.label}`}
        onClose={onClose}
      />
      <form onSubmit={onSubmit}>
        <ModalBody className="flex flex-col gap-3">
          <Field label="Name">
            <Input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Plex"
            />
          </Field>

          {properties.map(([key, meta]) => (
            <Field key={key} label={meta.title ?? key}>
              <IntegrationDynamicInput
                meta={meta}
                value={config[key]}
                onChange={(v) => setConfig({ ...config, [key]: v })}
              />
              {meta.description ? (
                <span className="text-[11px] text-muted-2">
                  {meta.description}
                </span>
              ) : null}
            </Field>
          ))}

          {kind.secret_fields.map((field) => (
            <Field key={field} label={field}>
              <Input
                required={!isEdit}
                type="password"
                value={secrets[field] ?? ""}
                onChange={(e) =>
                  setSecrets({ ...secrets, [field]: e.target.value })
                }
                placeholder={isEdit ? "Leave blank to keep existing" : "••••••••"}
              />
            </Field>
          ))}

          {error ? (
            <div className="text-[12px] text-sev-error">{error}</div>
          ) : null}
          {testResult ? (
            <div
              className={cn(
                "text-[12px] rounded-md border px-2 py-1.5",
                testResult.status === "ok"
                  ? "text-sev-ok border-sev-ok/40 bg-sev-ok/10"
                  : testResult.status === "degraded"
                    ? "text-sev-warn border-sev-warn/40 bg-sev-warn/10"
                    : "text-sev-error border-sev-error/40 bg-sev-error/10",
              )}
            >
              <span className="font-semibold capitalize">
                {testResult.status}
              </span>
              {testResult.detail ? <> · {testResult.detail}</> : null}
            </div>
          ) : null}
          {/* Stage 19 (audit follow-up): webhook secret. Edit-mode
              only — the integration must exist before we can attach
              a per-row secret. Kinds that don't ship a receiver
              (apprise, generic webhook) get nothing rendered. */}
          {isEdit && integration ? (
            <WebhookSection integration={integration} />
          ) : null}
        </ModalBody>
        <ModalFoot>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onTest}
            disabled={test.isPending}
            title={
              isEdit
                ? "Verify with the credentials in this dialog (enter secrets to test)"
                : "Verify the upstream is reachable without saving"
            }
          >
            <Icon name="refresh" size={12} />
            <span className="ml-1">
              {test.isPending ? "Testing…" : "Test"}
            </span>
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={submitPending}
          >
            <Icon name={isEdit ? "check" : "plus"} size={12} />
            <span className="ml-1">
              {submitPending
                ? isEdit
                  ? "Saving…"
                  : "Connecting…"
                : isEdit
                  ? "Save"
                  : "Connect"}
            </span>
          </Button>
        </ModalFoot>
      </form>
    </Modal>
  );
}

// ── Stage 19 (audit follow-up): webhook secret section ───────────

const WEBHOOK_RECEIVE_KINDS = new Set(["sonarr", "radarr", "plex", "jellyfin"]);

function WebhookSection({ integration }: { integration: Integration }) {
  const generate = useGenerateWebhookSecret();
  const [revealed, setRevealed] = useState<WebhookSecretResponse | null>(null);

  if (!WEBHOOK_RECEIVE_KINDS.has(integration.kind)) {
    return null;
  }

  const onGenerate = async () => {
    try {
      const result = await generate.mutateAsync(integration.id);
      setRevealed(result);
    } catch (err) {
      toast(`Failed to generate secret: ${(err as Error).message}`, "error");
    }
  };

  const copyToClipboard = (text: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard
        .writeText(text)
        .then(() => toast("Copied to clipboard", "ok"))
        .catch(() => toast("Couldn't copy", "warn"));
    }
  };

  return (
    <div
      className="mt-4 p-3 rounded-md border border-default bg-surface-2"
      data-testid="webhook-section"
    >
      <div className="text-[13px] font-semibold mb-1">
        Webhook receiver
      </div>
      <div className="text-[11.5px] text-muted-2 mb-2 leading-relaxed">
        Generate a per-integration secret so {integration.kind} can push
        file events here instead of waiting for the next poll. See the{" "}
        <a
          href="/docs/integrations/webhooks"
          className="underline"
          target="_blank"
          rel="noreferrer"
        >
          setup guide
        </a>{" "}
        for the upstream configuration steps.
      </div>
      {revealed ? (
        <div
          className="flex flex-col gap-1.5"
          data-testid="webhook-secret-revealed"
        >
          <div className="text-[11px] font-semibold text-sev-warn">
            This is the ONLY time the secret is shown. Copy it now.
          </div>
          <div className="flex items-center gap-1.5">
            <code className="flex-1 px-2 py-1 text-[11px] font-mono break-all bg-surface-1 rounded border border-default">
              {revealed.webhook_secret}
            </code>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => copyToClipboard(revealed.webhook_secret)}
              title="Copy secret"
            >
              <Icon name="check" size={12} />
            </Button>
          </div>
          <div className="text-[11px] text-muted-2">
            Webhook URL suffix:{" "}
            <code className="font-mono">{revealed.webhook_url_suffix}</code>
          </div>
        </div>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onGenerate}
          disabled={generate.isPending}
          aria-label="Generate webhook secret"
        >
          <Icon name="refresh" size={12} />
          <span className="ml-1">
            {generate.isPending ? "Generating…" : "Generate / rotate secret"}
          </span>
        </Button>
      )}
    </div>
  );
}
