/**
 * Automation schedule create dialog.
 *
 * Stage 5 introduced this dialog with a JSON textarea for the job
 * arguments. The Stage 9 audit follow-up replaced it with a fully
 * structured form. The Stage 9 close-out extracted the form widgets
 * to ``scheduleFormShared.tsx`` so the new edit dialog can share
 * them.
 *
 * The form-only pattern: every job kind's ``args_schema`` (already
 * published by the backend ``GET /automation/jobs`` endpoint, see
 * ``JobKind`` in ``useAutomation.ts``) drives typed inputs — strings,
 * numbers, booleans, and enums each get their own widget via
 * ``ArgInput``. Cron is exposed via the full vocabulary (minute,
 * hour, day, month, weekday) with preset shortcuts, all via
 * ``CronFieldset``.
 *
 * A read-only JSON preview lives under a collapsed ``<details>`` as
 * a power-user diagnostic — it shows the payload the form will
 * submit so an operator can verify the shape without leaving the
 * dialog. It is NOT an input. Pre-Stage-9 the JSON path was the
 * only path; now it's just a peek.
 */

import { useEffect, useMemo, useState, type FormEvent } from "react";

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
import { cn } from "@/lib/cn";
import {
  useCreateSchedule,
  type JobKind,
} from "@/hooks/useAutomation";

import {
  ArgInput,
  buildCronPayload,
  CronFieldset,
  initialArgsFor,
  PRESET_CRON,
  type CronPreset,
  type CronState,
} from "./scheduleFormShared";

export interface AutomationScheduleDialogProps {
  jobKinds: JobKind[];
  onClose: () => void;
}

export function AutomationScheduleDialog({
  jobKinds,
  onClose,
}: AutomationScheduleDialogProps) {
  const create = useCreateSchedule();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [jobKey, setJobKey] = useState(jobKinds[0]?.key ?? "");
  const [argsValues, setArgsValues] = useState<Record<string, unknown>>(() =>
    initialArgsFor(jobKinds[0]),
  );

  // Cron state — preset hydrates the five fields; switching preset
  // back to "custom" leaves them as-is so the operator can tweak.
  const [preset, setPreset] = useState<CronPreset>("daily");
  const [cron, setCron] = useState<CronState>(PRESET_CRON.daily);
  const [error, setError] = useState<string | null>(null);

  const selectedKind = useMemo(
    () => jobKinds.find((k) => k.key === jobKey),
    [jobKinds, jobKey],
  );

  // When the operator picks a different job kind, rehydrate the
  // args form so we don't carry over stale fields from the previous
  // kind's schema.
  useEffect(() => {
    setArgsValues(initialArgsFor(selectedKind));
  }, [selectedKind]);

  const cronPayload = useMemo(() => buildCronPayload(cron), [cron]);

  // Submission shape preview — what we'll actually POST. Stays in
  // sync with the form for the JSON-peek section below.
  const submissionPreview = useMemo(
    () => ({
      name: name.trim() || "<unset>",
      description: description.trim() || undefined,
      job_kind: jobKey,
      job_args: argsValues,
      cron: cronPayload,
    }),
    [name, description, jobKey, argsValues, cronPayload],
  );

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    // Surface required-args misses with a clear inline message
    // rather than relying on the server's 422.
    const required = selectedKind?.required_args ?? [];
    for (const req of required) {
      const v = argsValues[req];
      if (v === undefined || v === null || v === "") {
        setError(`Required argument "${req}" is missing.`);
        return;
      }
    }

    try {
      await create.mutateAsync({
        name,
        description: description || undefined,
        job_kind: jobKey,
        job_args: argsValues,
        cron: cronPayload,
      });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const argProps = selectedKind?.args_schema?.properties ?? {};
  const argRequired = new Set(selectedKind?.required_args ?? []);

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      ariaLabel="New schedule"
      size="md"
    >
      <ModalHead title="New schedule" onClose={onClose} />
      <form onSubmit={onSubmit}>
        <ModalBody className="flex flex-col gap-3">
          <Field label="Name">
            <Input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Nightly scan"
            />
          </Field>
          <Field label="Description (optional)">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this schedule does"
            />
          </Field>
          <Field label="Job">
            <Select
              value={jobKey}
              onChange={(e) => setJobKey(e.target.value)}
            >
              {jobKinds.map((k) => (
                <option key={k.key} value={k.key}>
                  {k.label}
                </option>
              ))}
            </Select>
            {selectedKind ? (
              <span className="text-[11px] text-muted-2">
                {selectedKind.description}
              </span>
            ) : null}
          </Field>

          {Object.entries(argProps).length > 0 ? (
            <fieldset
              className={cn(
                "flex flex-col gap-2 p-3 rounded-md",
                "border border-border bg-surface-sunk",
              )}
            >
              <legend className="px-1.5 text-[11.5px] text-muted-2 font-medium">
                Job arguments
              </legend>
              {Object.entries(argProps).map(([key, spec]) => (
                <ArgInput
                  key={key}
                  argKey={key}
                  spec={spec}
                  required={argRequired.has(key)}
                  value={argsValues[key]}
                  onChange={(next) =>
                    setArgsValues((prev) => {
                      const out = { ...prev };
                      if (next === undefined || next === "") {
                        delete out[key];
                      } else {
                        out[key] = next;
                      }
                      return out;
                    })
                  }
                />
              ))}
            </fieldset>
          ) : (
            <div className="text-[11.5px] text-muted-2">
              This job kind takes no arguments.
            </div>
          )}

          <CronFieldset
            preset={preset}
            cron={cron}
            onPresetChange={setPreset}
            onCronChange={setCron}
          />

          {/* Read-only JSON peek — diagnostic only, NOT an editor. */}
          <details className="text-[11.5px] text-muted-2">
            <summary className="cursor-pointer">
              Show JSON payload (read-only)
            </summary>
            <pre className="mt-1 p-2 rounded bg-surface-sunk overflow-auto text-[11px] leading-tight whitespace-pre-wrap break-words">
              {JSON.stringify(submissionPreview, null, 2)}
            </pre>
          </details>

          {error ? (
            <div role="alert" className="text-[12px] text-sev-error">
              {error}
            </div>
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
              {create.isPending ? "Creating…" : "Create"}
            </span>
          </Button>
        </ModalFoot>
      </form>
    </Modal>
  );
}
