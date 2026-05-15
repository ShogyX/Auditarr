/**
 * Optimization profile dialog (Stage 7 audit follow-up).
 *
 * Replaces the pre-Stage-7 single-textarea JSON editor with a
 * structured form that maps 1:1 to the backend's ``ProfileDefinition``
 * schema (video / audio / subtitles / output / advanced). The JSON
 * view survives as an "Advanced" collapsible block so power users
 * keep the escape hatch, kept bidirectionally synced with the form
 * inputs.
 *
 * Audit Issue 10 — "JSON-only profile editor is unusable for
 * operators". Pre-Stage-7, creating a working profile required the
 * operator to know the exact ProfileDefinition shape, the supported
 * codec / container vocabulary, and the JSON convention. Now: every
 * common knob has a labeled input with sensible defaults; the JSON
 * view is a debug aid, not the primary surface.
 *
 * Stage 7 also adds the optional "routing integration" selector
 * (sends jobs for this profile to a named integration vs. the
 * in-process ffmpeg runner). Backend column is in place; the
 * worker still ignores the value at this stage (deferred to the
 * first plugin landing), but the operator can configure it now so
 * existing profiles don't need a migration later.
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
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import {
  useCreateProfile,
  useUpdateProfile,
  type OptimizationProfile,
} from "@/hooks/useOptimization";
import { useIntegrations } from "@/hooks/useIntegrations";
import { cn } from "@/lib/cn";

// ── Vocabulary (mirrors backend profile_schema.py) ───────────────
//
// Kept in sync manually because the values are tiny and rarely
// change. A schema-driven generator was considered and rejected as
// over-engineering for ≤10 entries per dropdown.
const VIDEO_CODECS = ["libx265", "libx264", "libaom-av1", "copy"] as const;
const AUDIO_CODECS = ["libopus", "aac", "libmp3lame", "copy"] as const;
const CONTAINERS = ["mkv", "mp4", "webm"] as const;
const PRESETS = [
  "ultrafast",
  "superfast",
  "veryfast",
  "faster",
  "fast",
  "medium",
  "slow",
  "slower",
  "veryslow",
] as const;
const SCALE_OPTIONS = [
  { value: null, label: "Original" },
  { value: 4320, label: "8K (4320p)" },
  { value: 2160, label: "4K (2160p)" },
  { value: 1440, label: "1440p" },
  { value: 1080, label: "1080p" },
  { value: 720, label: "720p" },
  { value: 480, label: "480p" },
];

// ── Profile definition shape ─────────────────────────────────────
//
// Local TypeScript mirror of the backend ProfileDefinition. We use
// this as the canonical state shape; the JSON-view textarea
// serializes from / deserializes into it.
interface ProfileDefinition {
  video: {
    codec: string;
    crf: number | null;
    preset: string | null;
    max_bitrate_kbps: number | null;
    scale_height: number | null;
  };
  audio: {
    codec: string;
    bitrate_kbps: number | null;
    channels: number | null;
  };
  subtitles: {
    handling: "copy" | "drop";
  };
  output: {
    container: string;
    replace_input: boolean;
    keep_backup: boolean;
  };
  extra_args: string[];
  skip_if_bitrate_below_kbps: number | null;
}

const DEFAULT_DEFINITION: ProfileDefinition = {
  video: {
    codec: "libx265",
    crf: 22,
    preset: "medium",
    max_bitrate_kbps: null,
    scale_height: null,
  },
  audio: { codec: "copy", bitrate_kbps: null, channels: null },
  subtitles: { handling: "copy" },
  output: { container: "mkv", replace_input: true, keep_backup: true },
  extra_args: [],
  skip_if_bitrate_below_kbps: null,
};

/** Merge an incoming partial definition (from an existing profile)
 *  with the defaults, so partial / older shapes still render the
 *  full form. Missing nested keys fall through to defaults rather
 *  than throwing. */
function hydrate(raw: Record<string, unknown> | undefined): ProfileDefinition {
  const out: ProfileDefinition = JSON.parse(JSON.stringify(DEFAULT_DEFINITION));
  if (!raw || typeof raw !== "object") return out;
  const video = (raw as Record<string, unknown>).video;
  if (video && typeof video === "object") {
    Object.assign(out.video, video);
  }
  const audio = (raw as Record<string, unknown>).audio;
  if (audio && typeof audio === "object") {
    Object.assign(out.audio, audio);
  }
  const subtitles = (raw as Record<string, unknown>).subtitles;
  if (
    subtitles &&
    typeof subtitles === "object" &&
    ((subtitles as Record<string, unknown>).handling === "copy" ||
      (subtitles as Record<string, unknown>).handling === "drop")
  ) {
    out.subtitles.handling = (subtitles as Record<string, unknown>)
      .handling as "copy" | "drop";
  }
  const output = (raw as Record<string, unknown>).output;
  if (output && typeof output === "object") {
    Object.assign(out.output, output);
  }
  const extra = (raw as Record<string, unknown>).extra_args;
  if (Array.isArray(extra)) {
    out.extra_args = extra.filter((v): v is string => typeof v === "string");
  }
  const skip = (raw as Record<string, unknown>).skip_if_bitrate_below_kbps;
  if (typeof skip === "number" || skip === null) {
    out.skip_if_bitrate_below_kbps = skip;
  }
  return out;
}

/** Strip null-valued optional keys so the JSON view matches what the
 *  backend would persist (None ⇒ key absent in our convention). */
function toWire(def: ProfileDefinition): Record<string, unknown> {
  const out: Record<string, unknown> = {
    video: { codec: def.video.codec },
    audio: { codec: def.audio.codec },
    subtitles: { handling: def.subtitles.handling },
    output: {
      container: def.output.container,
      replace_input: def.output.replace_input,
      keep_backup: def.output.keep_backup,
    },
    extra_args: def.extra_args,
  };
  const v = out.video as Record<string, unknown>;
  if (def.video.crf !== null) v.crf = def.video.crf;
  if (def.video.preset !== null) v.preset = def.video.preset;
  if (def.video.max_bitrate_kbps !== null) {
    v.max_bitrate_kbps = def.video.max_bitrate_kbps;
  }
  if (def.video.scale_height !== null) {
    v.scale_height = def.video.scale_height;
  }
  const a = out.audio as Record<string, unknown>;
  if (def.audio.bitrate_kbps !== null) a.bitrate_kbps = def.audio.bitrate_kbps;
  if (def.audio.channels !== null) a.channels = def.audio.channels;
  if (def.skip_if_bitrate_below_kbps !== null) {
    out.skip_if_bitrate_below_kbps = def.skip_if_bitrate_below_kbps;
  }
  return out;
}

export interface OptimizationProfileDialogProps {
  /** ``null`` for create mode, an existing profile for edit. */
  profile: OptimizationProfile | null;
  onClose: () => void;
}

export function OptimizationProfileDialog({
  profile,
  onClose,
}: OptimizationProfileDialogProps) {
  const create = useCreateProfile();
  const update = useUpdateProfile();
  const integrations = useIntegrations();

  // Header fields (unchanged across the GUI / JSON split).
  const [name, setName] = useState(profile?.name ?? "");
  const [description, setDescription] = useState(profile?.description ?? "");
  const [enabled, setEnabled] = useState(profile?.enabled ?? true);
  const [integrationId, setIntegrationId] = useState<string | "">(
    profile?.optimization_integration_id ?? "",
  );

  // Canonical structured state.
  const [def, setDef] = useState<ProfileDefinition>(() =>
    hydrate(profile?.settings),
  );

  // Advanced JSON view — derived from def, with manual override.
  // When the operator types in the JSON area, we parse it back into
  // def on every successful parse so the two views stay in sync.
  const [showJson, setShowJson] = useState(false);
  const [jsonText, setJsonText] = useState(() =>
    JSON.stringify(toWire(hydrate(profile?.settings)), null, 2),
  );
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // When the form-side state changes, re-derive the JSON view so the
  // textarea reflects what the operator just toggled. Skip if the
  // textarea has focus (the operator is editing the JSON) to avoid
  // clobbering their work mid-edit.
  useEffect(() => {
    if (document.activeElement?.tagName === "TEXTAREA") return;
    setJsonText(JSON.stringify(toWire(def), null, 2));
    setJsonError(null);
  }, [def]);

  function onJsonChange(next: string) {
    setJsonText(next);
    try {
      const parsed = JSON.parse(next);
      setDef(hydrate(parsed));
      setJsonError(null);
    } catch (err) {
      setJsonError((err as Error).message);
    }
  }

  // Field-level helpers — keep the JSX below from getting nested
  // setState callback soup.
  function patchVideo(patch: Partial<ProfileDefinition["video"]>) {
    setDef((d) => ({ ...d, video: { ...d.video, ...patch } }));
  }
  function patchAudio(patch: Partial<ProfileDefinition["audio"]>) {
    setDef((d) => ({ ...d, audio: { ...d.audio, ...patch } }));
  }
  function patchOutput(patch: Partial<ProfileDefinition["output"]>) {
    setDef((d) => ({ ...d, output: { ...d.output, ...patch } }));
  }
  function setExtraArgs(args: string[]) {
    setDef((d) => ({ ...d, extra_args: args }));
  }

  const isPending = create.isPending || update.isPending;
  const title = profile
    ? `Edit profile · ${profile.name}`
    : "New optimization profile";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (jsonError) {
      setFormError(`Advanced JSON is invalid: ${jsonError}`);
      return;
    }
    const settings = toWire(def);
    try {
      if (profile) {
        await update.mutateAsync({
          id: profile.id,
          patch: {
            name,
            description: description || undefined,
            enabled,
            settings,
            optimization_integration_id: integrationId || null,
          },
        });
      } else {
        await create.mutateAsync({
          name,
          description: description || undefined,
          enabled,
          settings,
          optimization_integration_id: integrationId || null,
        });
      }
      onClose();
    } catch (err) {
      setFormError((err as Error).message);
    }
  }

  // Codec=copy disables the encoding knobs (CRF / preset / bitrate /
  // scale) because ffmpeg ignores them in passthrough mode.
  const videoCopy = def.video.codec === "copy";
  const audioCopy = def.audio.codec === "copy";

  // Surface integrations regardless of kind so any plugin can claim
  // a profile. The picker is informational at Stage 7 (worker
  // doesn't dispatch yet) but the value persists.
  const integrationOptions = useMemo(
    () =>
      (integrations.data ?? []).map((ig) => ({
        value: ig.id,
        label: `${ig.name} (${ig.kind})`,
      })),
    [integrations.data],
  );

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      ariaLabel={title}
      size="lg"
    >
      <ModalHead title={title} onClose={onClose} />
      <form onSubmit={onSubmit}>
        <ModalBody className="flex flex-col gap-3">
          {/* ── Header fields ── */}
          <Field label="Name">
            <Input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Shrink HEVC"
            />
          </Field>
          <Field label="Description (optional)">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this profile does"
            />
          </Field>
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            label="Enabled"
          />
          <Field label="Routing integration (optional)">
            <select
              className="settings-input"
              value={integrationId}
              onChange={(e) => setIntegrationId(e.target.value)}
            >
              <option value="">In-process ffmpeg runner (default)</option>
              {integrationOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-muted-2">
              When set, jobs for this profile will dispatch to the
              chosen integration once the routing wiring lands.
              In-process ffmpeg remains the default.
            </span>
          </Field>

          {/* ── Video ── */}
          <fieldset className="border border-border rounded-md p-3 flex flex-col gap-3">
            <legend className="text-[11px] uppercase tracking-wider text-muted-2 px-1">
              Video
            </legend>
            <Field label="Codec">
              <select
                className="settings-input"
                value={def.video.codec}
                onChange={(e) => patchVideo({ codec: e.target.value })}
              >
                {VIDEO_CODECS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={`CRF (${def.video.crf ?? "—"})`}>
              <input
                className="settings-input"
                type="range"
                min={0}
                max={51}
                step={1}
                disabled={videoCopy}
                value={def.video.crf ?? 22}
                onChange={(e) =>
                  patchVideo({ crf: Number(e.target.value) })
                }
                aria-label="CRF (constant rate factor)"
              />
              <span className="text-[11px] text-muted-2">
                Lower = higher quality. 18 visually lossless, 22–24
                typical, 28+ heavy compression. Ignored when codec is
                <code className="font-mono"> copy</code>.
              </span>
            </Field>
            <Field label="Preset">
              <select
                className="settings-input"
                value={def.video.preset ?? "medium"}
                disabled={videoCopy}
                onChange={(e) => patchVideo({ preset: e.target.value })}
              >
                {PRESETS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Max bitrate (kbps, optional)">
              <Input
                type="number"
                min={64}
                max={200000}
                disabled={videoCopy}
                value={def.video.max_bitrate_kbps ?? ""}
                onChange={(e) =>
                  patchVideo({
                    max_bitrate_kbps: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
                placeholder="Unbounded"
              />
            </Field>
            <Field label="Scale">
              <select
                className="settings-input"
                disabled={videoCopy}
                value={def.video.scale_height ?? ""}
                onChange={(e) =>
                  patchVideo({
                    scale_height: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
              >
                {SCALE_OPTIONS.map((o) => (
                  <option key={o.label} value={o.value ?? ""}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </fieldset>

          {/* ── Audio ── */}
          <fieldset className="border border-border rounded-md p-3 flex flex-col gap-3">
            <legend className="text-[11px] uppercase tracking-wider text-muted-2 px-1">
              Audio
            </legend>
            <Field label="Codec">
              <select
                className="settings-input"
                value={def.audio.codec}
                onChange={(e) => patchAudio({ codec: e.target.value })}
              >
                {AUDIO_CODECS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Bitrate (kbps, optional)">
              <Input
                type="number"
                min={24}
                max={2048}
                disabled={audioCopy}
                value={def.audio.bitrate_kbps ?? ""}
                onChange={(e) =>
                  patchAudio({
                    bitrate_kbps: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
                placeholder="Codec default"
              />
            </Field>
            <Field label="Channels (optional)">
              <Input
                type="number"
                min={1}
                max={8}
                disabled={audioCopy}
                value={def.audio.channels ?? ""}
                onChange={(e) =>
                  patchAudio({
                    channels: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
                placeholder="Same as source"
              />
            </Field>
          </fieldset>

          {/* ── Subtitles ── */}
          <Field label="Subtitles">
            <select
              className="settings-input"
              value={def.subtitles.handling}
              onChange={(e) =>
                setDef((d) => ({
                  ...d,
                  subtitles: {
                    handling: e.target.value as "copy" | "drop",
                  },
                }))
              }
            >
              <option value="copy">Copy all subtitle streams</option>
              <option value="drop">Drop subtitles</option>
            </select>
          </Field>

          {/* ── Output ── */}
          <fieldset className="border border-border rounded-md p-3 flex flex-col gap-3">
            <legend className="text-[11px] uppercase tracking-wider text-muted-2 px-1">
              Output
            </legend>
            <Field label="Container">
              <select
                className="settings-input"
                value={def.output.container}
                onChange={(e) => patchOutput({ container: e.target.value })}
              >
                {CONTAINERS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            <Switch
              checked={def.output.replace_input}
              onCheckedChange={(v) => patchOutput({ replace_input: v })}
              label="Replace input file after success"
            />
            <Switch
              checked={def.output.keep_backup}
              onCheckedChange={(v) => patchOutput({ keep_backup: v })}
              label="Keep a .bak file alongside the original"
            />
          </fieldset>

          {/* ── Advanced (extra args + skip threshold + JSON view) ── */}
          <fieldset className="border border-border rounded-md p-3 flex flex-col gap-3">
            <legend className="text-[11px] uppercase tracking-wider text-muted-2 px-1">
              Advanced
            </legend>
            <Field label="Extra ffmpeg arguments (one per line)">
              <Textarea
                value={def.extra_args.join("\n")}
                onChange={(e) =>
                  setExtraArgs(
                    e.target.value
                      .split("\n")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                rows={3}
                spellCheck={false}
                variant="mono"
              />
              <span className="text-[11px] text-muted-2">
                Inserted just before the output path. No validation —
                use sparingly.
              </span>
            </Field>
            <Field label="Skip when input bitrate below (kbps)">
              <Input
                type="number"
                min={0}
                max={200000}
                value={def.skip_if_bitrate_below_kbps ?? ""}
                onChange={(e) =>
                  setDef((d) => ({
                    ...d,
                    skip_if_bitrate_below_kbps: e.target.value
                      ? Number(e.target.value)
                      : null,
                  }))
                }
                placeholder="Always run"
              />
            </Field>
            <button
              type="button"
              onClick={() => setShowJson((s) => !s)}
              className="text-[12px] text-text-2 hover:underline self-start"
            >
              {showJson ? "Hide JSON view" : "Show JSON view"}
            </button>
            {showJson ? (
              <Field label="Settings (JSON)">
                <Textarea
                  variant="mono"
                  value={jsonText}
                  onChange={(e) => onJsonChange(e.target.value)}
                  spellCheck={false}
                  rows={12}
                  aria-invalid={jsonError !== null}
                  className={cn(
                    "bg-surface-sunk resize-y",
                    jsonError && "border-sev-error",
                  )}
                />
                {jsonError ? (
                  <span className="text-[11.5px] text-sev-error">
                    {jsonError}
                  </span>
                ) : (
                  <span className="text-[11.5px] text-muted-2">
                    Edits here are kept in sync with the form fields
                    above.
                  </span>
                )}
              </Field>
            ) : null}
          </fieldset>

          {formError ? (
            <div className="text-[12px] text-sev-error">{formError}</div>
          ) : null}
        </ModalBody>
        <ModalFoot>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={isPending || jsonError !== null}
          >
            <Icon name={profile ? "check" : "plus"} size={12} />
            <span className="ml-1">
              {isPending ? "Saving…" : profile ? "Save" : "Create"}
            </span>
          </Button>
        </ModalFoot>
      </form>
    </Modal>
  );
}
