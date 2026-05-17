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
import {
  useIntegrations,
  useIntegrationTranscodeProfiles,
  type TranscodeProfileSummary,
} from "@/hooks/useIntegrations";
import { useMediaVocabulary } from "@/hooks/useMedia";
import { cn } from "@/lib/cn";
import {
  OPTIONS_BY_TARGET,
  ROUTING_TARGET_LABELS,
  TRANSCODE_SCOPE_LABELS,
  getBrowserTimezone,
  type RoutingTarget,
} from "./optimizationShared";

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
  // ── Stage 07 (v1.7) ──────────────────────────────────────────
  transcode_scope: "video_and_audio" | "video_only" | "audio_only";
  tag_scope: string[];
  routing_target: RoutingTarget;
  schedule_window: {
    start_hour: number;
    end_hour: number;
    timezone: string;
  } | null;
  // ── Stage 08 (v1.7) ──────────────────────────────────────────
  // Free-form per-provider hints. The dialog populates
  // ``provider_profile_id`` here when the operator picks a
  // provider-side transcode profile; Plex callers may add
  // ``ratingKey`` / ``video_quality`` / ``video_resolution``.
  // The worker copies this dict through to the
  // ``TranscodeJobSpec.metadata`` field at submit time.
  provider_metadata: Record<string, unknown>;
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
  // Stage 07 (v1.7) defaults — both streams, no tag filter, in-
  // process runner, no schedule window.
  transcode_scope: "video_and_audio",
  tag_scope: [],
  routing_target: "in_process",
  schedule_window: null,
  // Stage 08 (v1.7) default — empty dict.
  provider_metadata: {},
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
  // ── Stage 07 (v1.7) ──────────────────────────────────────────
  const ts = (raw as Record<string, unknown>).transcode_scope;
  if (ts === "video_and_audio" || ts === "video_only" || ts === "audio_only") {
    out.transcode_scope = ts;
  }
  const tagScope = (raw as Record<string, unknown>).tag_scope;
  if (Array.isArray(tagScope)) {
    out.tag_scope = tagScope.filter(
      (v): v is string => typeof v === "string" && v.length > 0,
    );
  }
  const rt = (raw as Record<string, unknown>).routing_target;
  if (rt === "in_process" || rt === "plex" || rt === "jellyfin" || rt === "tdarr") {
    out.routing_target = rt;
  }
  const sw = (raw as Record<string, unknown>).schedule_window;
  if (sw && typeof sw === "object") {
    const swr = sw as Record<string, unknown>;
    const sh = swr.start_hour;
    const eh = swr.end_hour;
    const tz = swr.timezone;
    if (
      typeof sh === "number" &&
      typeof eh === "number" &&
      typeof tz === "string"
    ) {
      out.schedule_window = {
        start_hour: sh,
        end_hour: eh,
        timezone: tz,
      };
    }
  }
  // Stage 08 (v1.7) — provider_metadata is opaque to the dialog
  // except for the ``provider_profile_id`` key the picker
  // manipulates; pass the whole dict through.
  const pm = (raw as Record<string, unknown>).provider_metadata;
  if (pm && typeof pm === "object" && !Array.isArray(pm)) {
    out.provider_metadata = { ...(pm as Record<string, unknown>) };
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
  // ── Stage 07 (v1.7) ──────────────────────────────────────────
  // Always emit transcode_scope + routing_target so the backend
  // gets a fully-shaped definition (defaults are explicit). Only
  // emit tag_scope when non-empty (matches the backend's "empty
  // list = no requirement" semantics and keeps the JSON view
  // tidy for the common case). Schedule_window is null when the
  // operator hasn't enabled it.
  out.transcode_scope = def.transcode_scope;
  out.routing_target = def.routing_target;
  if (def.tag_scope.length > 0) {
    out.tag_scope = def.tag_scope;
  }
  if (def.schedule_window) {
    out.schedule_window = {
      start_hour: def.schedule_window.start_hour,
      end_hour: def.schedule_window.end_hour,
      timezone: def.schedule_window.timezone,
    };
  }
  // Stage 08 (v1.7) — only emit provider_metadata when non-empty
  // so the wire JSON stays tidy for in_process profiles.
  if (Object.keys(def.provider_metadata).length > 0) {
    out.provider_metadata = def.provider_metadata;
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

  // Stage 08 (v1.7) — fetch the provider-side transcode profiles
  // for the picker. The hook is disabled when integrationId is
  // empty (no integration row picked yet) so we don't spam the
  // API. Returns ``[]`` for providers that don't implement the
  // listing surface (Jellyfin shim).
  const providerProfiles = useIntegrationTranscodeProfiles(
    integrationId || null,
  );

  // Canonical structured state.
  const [def, setDef] = useState<ProfileDefinition>(() =>
    hydrate(profile?.settings),
  );

  // Stage 15 (plan §658) — pull the library vocabulary so the
  // tag-scope picker can offer the operator's actual tag set
  // as a datalist alongside the free-text input.
  const vocabulary = useMediaVocabulary();

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

  // Stage 07 (v1.7) — routing-target → option mask. The form
  // hides knobs that don't apply to the chosen target (per plan
  // §409): Plex/Jellyfin/Tdarr don't accept CRF / preset / etc;
  // the profile passes the abstract intent (codec family,
  // quality target) and Stage 08 translates per-integration.
  const routingOptions = OPTIONS_BY_TARGET[def.routing_target];

  // Stage 07 (v1.7) — browser-vs-server timezone mismatch.
  // The schedule window is evaluated against SERVER time, but
  // the dialog defaults the input to the BROWSER's tz. Surface
  // a small warning when the operator's local tz differs from
  // the configured window tz so they don't accidentally set
  // "10pm in Denver" when the server clock is in UTC. (Per
  // addendum B.5.)
  const browserTimezone = useMemo(getBrowserTimezone, []);
  const scheduleTzMismatch =
    def.schedule_window !== null &&
    def.schedule_window.timezone !== browserTimezone;

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
        {/* Stage 04 (v1.7) — bound the modal body height and let
            it scroll internally rather than overflowing the
            viewport. The form is long (codec, preset, CRF, audio,
            scope, tags, schedule …); on short screens or with
            many fields visible the previous behaviour clipped the
            ModalFoot's Save button below the fold. */}
        <ModalBody className="flex flex-col gap-3 max-h-[70vh] overflow-y-auto">
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

          {/* ── Stage 07 (v1.7) — Routing & schedule ── */}
          <fieldset
            className="border border-border rounded-md p-3 flex flex-col gap-3"
            data-testid="stage07-routing-fieldset"
          >
            <legend className="text-[11px] uppercase tracking-wider text-muted-2 px-1">
              Routing & schedule
            </legend>
            <Field label="Routing target">
              <select
                className="settings-input"
                value={def.routing_target}
                data-testid="routing-target-select"
                onChange={(e) =>
                  setDef((d) => ({
                    ...d,
                    routing_target: e.target.value as RoutingTarget,
                  }))
                }
              >
                {ROUTING_TARGET_LABELS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <span className="text-[11px] text-muted-2">
                In-process runs ffmpeg on this Auditarr host. The
                other targets hand the job to the named provider.
                Pick the matching integration row + a provider
                profile to complete the setup.
              </span>
            </Field>
            {/* Stage 08 (v1.7) — provider profile picker. Renders
                only when the operator has picked a non-in_process
                routing target AND a specific integration row.
                Reads ``GET /integrations/{id}/transcode-profiles``
                and writes the chosen id into
                ``provider_metadata.provider_profile_id``. */}
            {def.routing_target !== "in_process" && integrationId ? (
              <Field label="Provider profile">
                <select
                  className="settings-input"
                  data-testid="provider-profile-select"
                  value={
                    (def.provider_metadata.provider_profile_id as
                      | string
                      | undefined) ?? ""
                  }
                  disabled={providerProfiles.isLoading}
                  onChange={(e) =>
                    setDef((d) => {
                      const next = { ...d.provider_metadata };
                      if (e.target.value) {
                        next.provider_profile_id = e.target.value;
                      } else {
                        delete next.provider_profile_id;
                      }
                      return { ...d, provider_metadata: next };
                    })
                  }
                >
                  <option value="">— Select —</option>
                  {(providerProfiles.data ?? []).map(
                    (p: TranscodeProfileSummary) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.description ? ` — ${p.description}` : ""}
                      </option>
                    ),
                  )}
                </select>
                <span className="text-[11px] text-muted-2">
                  {providerProfiles.isLoading
                    ? "Loading profiles…"
                    : providerProfiles.isError
                      ? "Couldn't reach the integration; pick one once it's healthy."
                      : (providerProfiles.data ?? []).length === 0
                        ? "No provider profiles available. Some integrations (e.g. Jellyfin) don't expose this surface."
                        : "Picked profile is referenced when submitting transcode jobs."}
                </span>
              </Field>
            ) : null}
            <Field label="Transcode scope">
              <select
                className="settings-input"
                value={def.transcode_scope}
                data-testid="transcode-scope-select"
                onChange={(e) =>
                  setDef((d) => ({
                    ...d,
                    transcode_scope: e.target.value as
                      | "video_and_audio"
                      | "video_only"
                      | "audio_only",
                  }))
                }
              >
                {TRANSCODE_SCOPE_LABELS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <span className="text-[11px] text-muted-2">
                ``video_only`` forces ``-c:a copy``; ``audio_only``
                forces ``-c:v copy``. The unselected stream's
                codec block is ignored.
              </span>
            </Field>
            <Field label="Tag scope (optional)">
              <Input
                value={def.tag_scope.join(", ")}
                placeholder="plex-incompatible-video, 4k"
                data-testid="tag-scope-input"
                list="profile-tag-scope-vocab"
                onChange={(e) => {
                  const next = e.target.value
                    .split(",")
                    .map((t) => t.trim())
                    .filter((t) => t.length > 0);
                  // Dedup client-side; backend also dedups.
                  const dedup: string[] = [];
                  const seen = new Set<string>();
                  for (const t of next) {
                    if (!seen.has(t)) {
                      dedup.push(t);
                      seen.add(t);
                    }
                  }
                  setDef((d) => ({ ...d, tag_scope: dedup }));
                }}
              />
              {/* Stage 15 (plan §658) — datalist offers the
                  library's actual tag set as autocomplete
                  hints. The input stays free-text so operators
                  can author profiles for tags not yet
                  applied. */}
              <datalist
                id="profile-tag-scope-vocab"
                data-testid="profile-tag-scope-datalist"
              >
                {(vocabulary.data?.tags ?? []).map((tag) => (
                  <option key={tag} value={tag} />
                ))}
              </datalist>
              <span className="text-[11px] text-muted-2">
                Comma-separated. Files must carry every listed tag
                to be eligible for this profile. The rule engine
                rejects queue attempts for files missing any tag.
              </span>
            </Field>
            {/* ── Schedule window ── */}
            <div className="flex flex-col gap-2">
              <Switch
                checked={def.schedule_window !== null}
                onCheckedChange={(on) => {
                  if (on) {
                    setDef((d) => ({
                      ...d,
                      schedule_window: {
                        start_hour: 22,
                        end_hour: 6,
                        timezone: browserTimezone,
                      },
                    }));
                  } else {
                    setDef((d) => ({ ...d, schedule_window: null }));
                  }
                }}
                label="Restrict to a daily schedule window"
              />
              {def.schedule_window !== null ? (
                <div
                  className="grid grid-cols-3 gap-2"
                  data-testid="schedule-window-controls"
                >
                  <Field label="Start hour (0-23)">
                    <Input
                      type="number"
                      min={0}
                      max={23}
                      value={def.schedule_window.start_hour}
                      data-testid="schedule-start-hour"
                      onChange={(e) =>
                        setDef((d) =>
                          d.schedule_window
                            ? {
                                ...d,
                                schedule_window: {
                                  ...d.schedule_window,
                                  start_hour: Math.max(
                                    0,
                                    Math.min(23, Number(e.target.value)),
                                  ),
                                },
                              }
                            : d,
                        )
                      }
                    />
                  </Field>
                  <Field label="End hour (0-23)">
                    <Input
                      type="number"
                      min={0}
                      max={23}
                      value={def.schedule_window.end_hour}
                      data-testid="schedule-end-hour"
                      onChange={(e) =>
                        setDef((d) =>
                          d.schedule_window
                            ? {
                                ...d,
                                schedule_window: {
                                  ...d.schedule_window,
                                  end_hour: Math.max(
                                    0,
                                    Math.min(23, Number(e.target.value)),
                                  ),
                                },
                              }
                            : d,
                        )
                      }
                    />
                  </Field>
                  <Field label="Timezone">
                    <Input
                      value={def.schedule_window.timezone}
                      placeholder="UTC"
                      data-testid="schedule-timezone"
                      onChange={(e) =>
                        setDef((d) =>
                          d.schedule_window
                            ? {
                                ...d,
                                schedule_window: {
                                  ...d.schedule_window,
                                  timezone: e.target.value,
                                },
                              }
                            : d,
                        )
                      }
                    />
                  </Field>
                </div>
              ) : null}
              {scheduleTzMismatch ? (
                <div
                  className="text-[11px] text-sev-warn"
                  data-testid="schedule-tz-mismatch-warning"
                >
                  <Icon name="alert" size={10} className="inline mr-1" />
                  Your browser is in <code>{browserTimezone}</code>; this
                  window's timezone is{" "}
                  <code>{def.schedule_window?.timezone}</code>. The
                  window is evaluated against server time, not
                  your local clock.
                </div>
              ) : null}
            </div>
          </fieldset>

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
                disabled={videoCopy || !routingOptions.crf}
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
                {!routingOptions.crf ? (
                  <>
                    {" "}
                    The chosen routing target ignores CRF — the
                    provider manages quality internally.
                  </>
                ) : null}
              </span>
            </Field>
            {routingOptions.preset ? (
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
            ) : null}
            {routingOptions.max_bitrate_kbps ? (
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
            ) : null}
            {routingOptions.scale_height ? (
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
            ) : null}
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
            {routingOptions.extra_args ? (
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
                  use sparingly. Only meaningful for the in-process
                  ffmpeg runner.
                </span>
              </Field>
            ) : null}
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
