/**
 * Stage 6 — Notification channel create dialog.
 *
 * Adopts the Stage 1 ``Modal`` primitive. Replaces the hand-rolled
 * ``fixed inset-0`` overlay + manual Escape handler from the
 * pre-Stage-6 dialog.
 *
 * Form fields:
 *   - Name (always required)
 *   - Per-property config (rendered by ``NotificationDynamicInput``
 *     which understands string/integer/boolean/enum)
 *   - Per-secret-field (rendered as password inputs)
 *   - Severity threshold (one of six fixed ranks, default Warn-or-higher)
 *
 * The "severity threshold" copy explains the operational semantics:
 * lower-severity alerts are still *recorded* in the delivery log as
 * "skipped", so the operator can tune the threshold based on actual
 * traffic rather than guessing.
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
import { Select } from "@/components/ui/Select";
import {
  useCreateChannel,
  type NotificationKind,
} from "@/hooks/useNotifications";

import { NotificationDynamicInput } from "./NotificationDynamicInput";
import {
  SEVERITY_RANK_OPTIONS,
  initialConfig,
} from "./notificationsShared";

export interface NotificationChannelDialogProps {
  kind: NotificationKind;
  onClose: () => void;
}

export function NotificationChannelDialog({
  kind,
  onClose,
}: NotificationChannelDialogProps) {
  const create = useCreateChannel();
  const [name, setName] = useState(`${kind.label}`);
  const [config, setConfig] = useState<Record<string, unknown>>(() =>
    initialConfig(kind),
  );
  const [secrets, setSecrets] = useState<Record<string, string>>(() =>
    Object.fromEntries(kind.secret_fields.map((s) => [s, ""])),
  );
  const [minRank, setMinRank] = useState(40);
  const [error, setError] = useState<string | null>(null);

  const properties = useMemo(
    () => Object.entries(kind.config_schema.properties ?? {}),
    [kind],
  );

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await create.mutateAsync({
        name,
        kind: kind.kind,
        config,
        secrets,
        min_severity_rank: minRank,
      });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      ariaLabel={`Add ${kind.label} channel`}
      size="md"
    >
      <ModalHead title={`Add ${kind.label} channel`} onClose={onClose} />
      <form onSubmit={onSubmit}>
        <ModalBody className="flex flex-col gap-3">
          <Field label="Name">
            <Input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>

          {properties.map(([key, meta]) => (
            <Field key={key} label={(meta.title as string) ?? key}>
              <NotificationDynamicInput
                meta={meta}
                value={config[key]}
                onChange={(v) => setConfig({ ...config, [key]: v })}
              />
              {meta.description ? (
                <span className="text-[11px] text-muted-2">
                  {meta.description as string}
                </span>
              ) : null}
            </Field>
          ))}

          {kind.secret_fields.map((field) => (
            <Field key={field} label={field}>
              <Input
                type="password"
                value={secrets[field] ?? ""}
                onChange={(e) =>
                  setSecrets({ ...secrets, [field]: e.target.value })
                }
                placeholder="••••••••"
              />
            </Field>
          ))}

          <Field label="Severity threshold">
            <Select
              value={minRank}
              onChange={(e) => setMinRank(Number(e.target.value))}
            >
              {SEVERITY_RANK_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
            <span className="text-[11px] text-muted-2">
              This channel only fires when the rule's severity meets the
              threshold. Lower-severity alerts are recorded as ‘skipped’
              in the delivery log.
            </span>
          </Field>

          {error ? (
            <div className="text-[12px] text-sev-error">{error}</div>
          ) : null}
        </ModalBody>
        <ModalFoot>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={create.isPending}
          >
            <Icon name="plus" size={12} />
            <span className="ml-1">
              {create.isPending ? "Creating…" : "Create channel"}
            </span>
          </Button>
        </ModalFoot>
      </form>
    </Modal>
  );
}
