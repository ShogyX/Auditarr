/**
 * Stage 6 — Plugin settings dialog.
 *
 * Adopts the Stage 1 ``Modal`` primitive (was the ``.dialog-*`` CSS
 * family from Stage 22). Functional behaviour is unchanged from
 * Stage 14/25:
 *
 *   - schema + persisted values fetched in parallel
 *   - textarea seeded once after both queries resolve (the "did we
 *     seed?" flag prevents an infinite re-render in strict mode and
 *     protects against the empty-payload corner case)
 *   - JSON parses live; the Save button is disabled until valid
 *
 * The Escape-to-close keyboard handler is now handled by Radix Dialog
 * (Modal wraps it), so the manual ``window.addEventListener("keydown")``
 * effect block is gone. Same a11y semantics, less code.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
} from "@/components/ui/Modal";
import { Textarea } from "@/components/ui/Textarea";
import {
  usePluginSchema,
  usePluginSettings,
  usePutPluginSettings,
  type PluginSummary,
} from "@/hooks/usePlugins";
import { cn } from "@/lib/cn";

export interface PluginSettingsDialogProps {
  plugin: PluginSummary;
  onClose: () => void;
}

export function PluginSettingsDialog({
  plugin,
  onClose,
}: PluginSettingsDialogProps) {
  const schemaQuery = usePluginSchema(plugin.id);
  const settingsQuery = usePluginSettings(plugin.id);
  const put = usePutPluginSettings();
  const [text, setText] = useState<string>("");
  const [seeded, setSeeded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Seed the textarea once after both queries have settled. The
  // ``seeded`` flag is essential: without it, the empty-object
  // string ``"{}"`` could match the empty initial state and trigger
  // a re-seed every render, clobbering user edits.
  useEffect(() => {
    if (seeded) return;
    if (schemaQuery.isLoading || settingsQuery.isLoading) return;
    const persisted = settingsQuery.data?.values;
    const defaults = schemaQuery.data?.defaults;
    const initial = persisted ?? defaults ?? {};
    setText(JSON.stringify(initial, null, 2));
    setSeeded(true);
  }, [
    seeded,
    schemaQuery.isLoading,
    settingsQuery.isLoading,
    schemaQuery.data,
    settingsQuery.data,
  ]);

  const parsed = (() => {
    try {
      return { ok: true as const, value: JSON.parse(text || "{}") };
    } catch (err) {
      return { ok: false as const, error: (err as Error).message };
    }
  })();

  async function onSave() {
    setError(null);
    if (!parsed.ok) {
      setError(`Invalid JSON: ${parsed.error}`);
      return;
    }
    try {
      await put.mutateAsync({
        pluginId: plugin.id,
        values: parsed.value as Record<string, unknown>,
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
      ariaLabel={`${plugin.name} settings`}
      size="lg"
    >
      <ModalHead
        title={`${plugin.name} settings`}
        subtitle={`Plugin · ${plugin.type}`}
        onClose={onClose}
      />
      <ModalBody className="flex flex-col gap-3">
        {plugin.last_error ? (
          <div className="runtime-warn">
            <Icon
              name="alert"
              size={14}
              className="text-sev-error shrink-0 mt-0.5"
            />
            <span className="font-mono text-[11.5px]">{plugin.last_error}</span>
          </div>
        ) : null}

        {schemaQuery.data?.schema === null ? (
          <p className="text-[12px] text-muted m-0">
            This plugin doesn't declare a settings schema. Any JSON object
            is accepted; the plugin reads its own keys.
          </p>
        ) : null}

        <Textarea
          variant="mono"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          rows={14}
          aria-invalid={!parsed.ok}
          className={cn(
            "bg-surface-sunk resize-y",
            !parsed.ok && "border-sev-error",
          )}
        />
        {!parsed.ok ? (
          <div className="text-[11.5px] text-sev-error">{parsed.error}</div>
        ) : null}
        {error ? (
          <div className="text-[12px] text-sev-error">{error}</div>
        ) : null}
      </ModalBody>
      <ModalFoot>
        <Button size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="accent"
          onClick={onSave}
          disabled={put.isPending || !parsed.ok}
        >
          <Icon name="check" size={12} />
          <span className="ml-1">{put.isPending ? "Saving…" : "Save"}</span>
        </Button>
      </ModalFoot>
    </Modal>
  );
}
