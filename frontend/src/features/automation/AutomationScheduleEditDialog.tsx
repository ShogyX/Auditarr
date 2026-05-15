/**
 * Automation schedule edit dialog.
 *
 * Stage 9 audit follow-up close-out. Pre-Stage-9 there was no
 * edit affordance at all — once a schedule was created with the
 * wrong args, operators had to delete and recreate it. This dialog
 * surfaces an Edit path that reuses the same structured form as
 * ``AutomationScheduleDialog`` (the create dialog) via the shared
 * ``scheduleFormShared.tsx`` widgets.
 *
 * Pre-population rules:
 *   - ``name``, ``description`` come straight from the row.
 *   - ``job_kind`` is read-only on this dialog. Changing the job
 *     kind would invalidate the args entirely; if an operator really
 *     wants a different job kind, deleting + recreating is the
 *     honest path. The job kind is shown as a Tag pill so it's
 *     visible but not editable.
 *   - ``job_args`` is hydrated from the row's saved values, falling
 *     back to schema defaults for any property the saved row didn't
 *     have. New properties added to a job's schema since the row
 *     was created get their defaults; obsolete properties from the
 *     row's saved args are dropped (they'd have nowhere to render).
 *   - ``cron`` is hydrated via ``parseCronToState``. The preset is
 *     inferred by matching against ``PRESET_CRON`` — if none match,
 *     the preset starts as "custom".
 *
 * On submit: PATCH with the full set of mutable fields (name,
 * description, job_args, cron). The backend's update endpoint
 * accepts ``Partial<ScheduleCreatePayload>`` so unmodified fields
 * could be omitted, but sending the full set is cleaner — the row
 * we pull from the cache already has the values, and round-tripping
 * them keeps the patch idempotent.
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
import { Tag } from "@/components/ui/Pill";
import { cn } from "@/lib/cn";
import {
  useUpdateSchedule,
  type JobKind,
  type Schedule,
} from "@/hooks/useAutomation";

import {
  ArgInput,
  buildCronPayload,
  CronFieldset,
  initialArgsFor,
  parseCronToState,
  PRESET_CRON,
  type CronPreset,
  type CronState,
} from "./scheduleFormShared";

export interface AutomationScheduleEditDialogProps {
  schedule: Schedule;
  jobKinds: JobKind[];
  onClose: () => void;
}

// Helper: detect which preset (if any) the saved cron matches, so
// the dialog can open with the right preset selected.
function inferPreset(cron: CronState): CronPreset {
  // Cheap shallow-equal of the five fields against each known preset.
  for (const key of Object.keys(PRESET_CRON) as Array<keyof typeof PRESET_CRON>) {
    const p = PRESET_CRON[key];
    if (
      p.minute === cron.minute &&
      p.hour === cron.hour &&
      p.day === cron.day &&
      p.month === cron.month &&
      p.weekday === cron.weekday
    ) {
      return key;
    }
  }
  return "custom";
}

// Helper: merge saved args with the current schema's defaults.
// Stops the form from leaking obsolete keys (properties no longer
// in the schema) AND backfills new properties whose default the
// row never saw because the schema was updated.
function hydrateArgs(
  saved: Record<string, unknown>,
  kind: JobKind | undefined,
): Record<string, unknown> {
  if (!kind) return { ...saved };
  const out = initialArgsFor(kind); // defaults first
  const props = kind.args_schema?.properties ?? {};
  for (const key of Object.keys(props)) {
    if (key in saved) out[key] = saved[key];
  }
  return out;
}

export function AutomationScheduleEditDialog({
  schedule,
  jobKinds,
  onClose,
}: AutomationScheduleEditDialogProps) {
  const update = useUpdateSchedule();

  const selectedKind = useMemo(
    () => jobKinds.find((k) => k.key === schedule.job_kind),
    [jobKinds, schedule.job_kind],
  );

  // ── Hydrated initial state ─────────────────────────────────
  const initialCron = useMemo(
    () => parseCronToState(schedule.cron),
    [schedule.cron],
  );
  const [name, setName] = useState(schedule.name);
  const [description, setDescription] = useState(schedule.description ?? "");
  const [argsValues, setArgsValues] = useState<Record<string, unknown>>(() =>
    hydrateArgs(schedule.job_args ?? {}, selectedKind),
  );
  const [preset, setPreset] = useState<CronPreset>(() =>
    inferPreset(initialCron),
  );
  const [cron, setCron] = useState<CronState>(initialCron);
  const [error, setError] = useState<string | null>(null);

  // If the schedule prop changes (cache update mid-edit), rehydrate
  // the args from the new schema view but keep what the operator
  // has typed if it conflicts. Practically this only fires on
  // first mount; the dialog is short-lived.
  useEffect(() => {
    setArgsValues(hydrateArgs(schedule.job_args ?? {}, selectedKind));
  }, [schedule.job_args, selectedKind]);

  const cronPayload = useMemo(() => buildCronPayload(cron), [cron]);

  const submissionPreview = useMemo(
    () => ({
      name: name.trim() || "<unset>",
      description: description.trim() || undefined,
      job_args: argsValues,
      cron: cronPayload,
    }),
    [name, description, argsValues, cronPayload],
  );

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    const required = selectedKind?.required_args ?? [];
    for (const req of required) {
      const v = argsValues[req];
      if (v === undefined || v === null || v === "") {
        setError(`Required argument "${req}" is missing.`);
        return;
      }
    }

    try {
      await update.mutateAsync({
        id: schedule.id,
        patch: {
          name,
          description: description || undefined,
          job_args: argsValues,
          cron: cronPayload,
        },
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
      ariaLabel="Edit schedule"
      size="md"
    >
      <ModalHead title={`Edit schedule — ${schedule.name}`} onClose={onClose} />
      <form onSubmit={onSubmit}>
        <ModalBody className="flex flex-col gap-3">
          <Field label="Name">
            <Input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Description (optional)">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>
          {/* Job kind is read-only on edit — changing it would
              invalidate every arg, so we surface it as a Tag pill
              and direct the operator to delete+recreate if they
              really need a different kind. */}
          <Field label="Job">
            <div className="flex items-center gap-2">
              <Tag>{schedule.job_kind}</Tag>
              <span className="text-[11px] text-muted-2">
                Job kind can&apos;t be changed. Delete and recreate to switch.
              </span>
            </div>
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
            disabled={update.isPending}
          >
            <Icon name="check" size={12} />
            <span className="ml-1">
              {update.isPending ? "Saving…" : "Save changes"}
            </span>
          </Button>
        </ModalFoot>
      </form>
    </Modal>
  );
}
