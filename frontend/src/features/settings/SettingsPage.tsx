import type { FormEvent, ReactNode } from "react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/shell/PageHeader";
import { ErrorBoundary } from "@/components/shell/ErrorBoundary";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, LoadingState } from "@/components/ui/States";
import { useHelpKey } from "@/hooks/useHelpKey";
import {
  useCreateLibrary,
  useDeleteLibrary,
  useLibraries,
  useTriggerScan,
  useUpdateLibrary,
  type Library,
} from "@/hooks/useMedia";
import { useSystemConfig, type SystemConfig } from "@/hooks/useSystem";
import { ACCENTS, type AccentName } from "@/lib/accent";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { useUiStore } from "@/stores/uiStore";

import { HousekeepingActionsCard } from "./HousekeepingActionsCard";
import { LibraryEditDialog } from "./LibraryEditDialog";
import { RuntimeSettingsPanel } from "./RuntimeSettingsPanel";
import { SecretsPanel } from "./SecretsPanel";
import { SystemMaintenanceCard } from "./SystemMaintenanceCard";

export function SettingsPage() {
  useHelpKey("settings.admin");
  const theme = useUiStore((s) => s.theme);
  const accent = useUiStore((s) => s.accent);
  const nav = useUiStore((s) => s.nav);
  const setMany = useUiStore((s) => s.setMany);

  // Stage 6 audit fix (Issue 8): the page was a single ~700-line
  // scroll. Splitting into category tabs keeps each view short
  // without restructuring any of the section cards. The tab
  // vocabulary mirrors the audit's grouping:
  //   - Workspace   : Libraries + Appearance + Path-mappings summary
  //   - System      : Runtime settings + Secrets + System config + Housekeeping
  //   - Integrations: Path mappings
  //   - Security    : VirusTotal + Account security
  // Default tab is "workspace" — the surface most operators reach
  // for during day-to-day work.
  const [tab, setTab] = useState<SettingsTab>("workspace");
  // Stage 7 (audit follow-up): the System tab now hosts a sub-tab
  // strip so Runtime / Secrets / System config / Housekeeping
  // don't all stack into a single long scroll. Default is
  // "runtime" — the panel that drives the most day-to-day edits.
  const [systemSubTab, setSystemSubTab] = useState<SystemSubTab>("runtime");

  return (
    <>
      <PageHeader
        title="Settings"
        sub="Workspace appearance · libraries · admin tools"
        helpKey="settings.admin"
      />
      <div className="p-6 flex flex-col gap-6 max-w-7xl xl:max-w-none">
        {/* Stage 6: settings sub-category tabs. Reuses the
            ``.segmented`` primitive — same component vocabulary as
            ``RulesTabBar`` — so no new CSS is introduced. role/aria
            wiring matches Rules so assistive tech sees the same
            tablist contract everywhere in the app. */}
        <div className="segmented" role="tablist" aria-label="Settings sections">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "workspace"}
            className={tab === "workspace" ? "on" : ""}
            onClick={() => setTab("workspace")}
          >
            Workspace
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "system"}
            className={tab === "system" ? "on" : ""}
            onClick={() => setTab("system")}
          >
            System
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "security"}
            className={tab === "security" ? "on" : ""}
            onClick={() => setTab("security")}
          >
            Security
          </button>
        </div>

        {tab === "workspace" ? (
          <>
            {/* Local ErrorBoundary: if a single library row blows up
                in render (e.g. a stale ID is referenced after a delete
                and React's reconciler trips), we want the rest of the
                Settings page to keep working rather than unmounting
                the whole app shell. */}
            <ErrorBoundary>
              <LibrariesCard />
            </ErrorBoundary>
            <Card>
              <CardHead title="Appearance" />
              <CardBody>
                <Field label="Theme">
                  <ChoiceRow>
                    {(["light", "dark"] as const).map((t) => (
                      <Choice key={t} selected={theme === t} onClick={() => setMany({ theme: t })}>
                        <Icon name={t === "dark" ? "moon" : "sun"} size={12} />
                        {t}
                      </Choice>
                    ))}
                  </ChoiceRow>
                </Field>

                <Field label="Accent">
                  <div className="flex gap-2 flex-wrap">
                    {(Object.keys(ACCENTS) as AccentName[]).map((name) => {
                      const a = ACCENTS[name];
                      const selected = accent === name;
                      return (
                        <button
                          key={name}
                          type="button"
                          onClick={() => setMany({ accent: name })}
                          className="px-1 py-1 inline-flex items-center gap-1.5 rounded-[6px] border text-[12px]"
                          style={{
                            background: selected ? "var(--surface-sunk)" : "transparent",
                            borderColor: selected ? "var(--border-strong)" : "var(--border)",
                          }}
                        >
                          <span
                            className="block h-4 w-4 rounded-[3px]"
                            style={{ background: `oklch(${a.l} ${a.c} ${a.h})` }}
                          />
                          <span className="pr-1 capitalize text-text-2">{name}</span>
                        </button>
                      );
                    })}
                  </div>
                </Field>

                <Field label="Navigation">
                  <ChoiceRow>
                    {(["sidebar", "top"] as const).map((n) => (
                      <Choice key={n} selected={nav === n} onClick={() => setMany({ nav: n })}>
                        {n === "top" ? "Top tabs" : "Sidebar"}
                      </Choice>
                    ))}
                  </ChoiceRow>
                </Field>
              </CardBody>
            </Card>

            {/* Stage 7 (audit follow-up): summary card that points at
                the dedicated Integrations → Path mappings tab. The
                full editor is heavy; a 3-line summary here keeps the
                workspace overview short while making the surface
                discoverable to operators who don't think to click
                "Integrations".
                v1.9 Stage 2.1: target is now /integrations, not the
                Settings → Integrations sub-tab (which no longer
                exists). */}
            <PathMappingsSummaryCard />
          </>
        ) : null}

        {tab === "system" ? (
          <>
            {/* Stage 7 (audit follow-up): System sub-tab strip.
                Pre-Stage-7 every System panel stacked vertically into
                a single 1500-line scroll. The strip keeps each panel
                under one screenful and matches the four-bucket model
                from the audit plan (Runtime / Secrets / System
                config / Housekeeping). Reuses the same ``.segmented``
                primitive as the top-level tab bar so visual style
                is consistent. role="tablist" + aria-selected so
                assistive tech sees the same contract as the outer
                strip. */}
            <div
              className="segmented"
              role="tablist"
              aria-label="System sub-sections"
            >
              <button
                type="button"
                role="tab"
                aria-selected={systemSubTab === "runtime"}
                className={systemSubTab === "runtime" ? "on" : ""}
                onClick={() => setSystemSubTab("runtime")}
              >
                Runtime
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={systemSubTab === "secrets"}
                className={systemSubTab === "secrets" ? "on" : ""}
                onClick={() => setSystemSubTab("secrets")}
              >
                Secrets
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={systemSubTab === "config"}
                className={systemSubTab === "config" ? "on" : ""}
                onClick={() => setSystemSubTab("config")}
              >
                System config
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={systemSubTab === "housekeeping"}
                className={systemSubTab === "housekeeping" ? "on" : ""}
                onClick={() => setSystemSubTab("housekeeping")}
              >
                Housekeeping
              </button>
            </div>

            {systemSubTab === "runtime" ? (
              /* Stage 22: schema-driven runtime settings editor.
                 The describe + values endpoints (Stage 21) feed a
                 category-rail panel; each editable field is rendered
                 from its schema entry rather than hand-written here, so
                 adding new fields server-side surfaces them automatically.
                 Admin-only — non-admins see an empty admin-required
                 state. Dirty edits are batched into a confirm dialog
                 before being applied; ``immediate`` keys take effect on
                 save, ``next_tick`` keys apply on the next scheduler
                 tick. Restoring a default routes to DELETE so the
                 override table stays minimal. */
              <RuntimeSettingsPanel />
            ) : null}

            {systemSubTab === "secrets" ? (
              /* Stage 22: encrypted-secrets editor. Plaintext NEVER
                 round-trips — the panel reads metadata only (set?
                 last_tested? last_test_ok?) and accepts new values via a
                 one-way PUT. */
              <SecretsPanel />
            ) : null}

            {systemSubTab === "config" ? (
              /* Stage 20: read-only operational config. Editing these
                 requires changing the env file and restarting the
                 service. */
              <SystemConfigCards />
            ) : null}

            {systemSubTab === "housekeeping" ? (
              /* Stage 7 (audit follow-up): the Housekeeping sub-tab
                 is the RuntimeSettingsPanel scoped to a single
                 category — same code path, less surface area. The
                 RuntimeSettingsPanel hides its category rail when
                 ``categoryFilter`` is set.

                 Stage 14 (audit follow-up): two admin-only cards
                 above the retention panel:
                   - HousekeepingActionsCard — Run now + last-run.
                   - SystemMaintenanceCard   — Docs reload. */
              <>
                <HousekeepingActionsCard />
                <SystemMaintenanceCard />
                <RuntimeSettingsPanel categoryFilter="housekeeping" />
              </>
            ) : null}
          </>
        ) : null}

        {tab === "security" ? (
          <>
            {/* Stage 7 (audit follow-up): self-service account
                security entry-point. Surfaces sessions / sign-out-
                everywhere / password as a single discoverable card
                inside the Security tab rather than buried behind the
                avatar in TopNav. The card defers to /account for the
                actual forms — duplicating the forms here would mean
                two surfaces to keep in sync. */}
            <AccountSecurityCard />

            {/* Stage 14 (audit follow-up): audit log entry. The page
                itself lives at /settings/audit; this card surfaces a
                discoverable link rather than a hidden URL. */}
            <Card>
              <CardBody>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <div className="text-[13px] font-semibold">
                      Audit log
                    </div>
                    <div className="text-[11.5px] text-muted-2">
                      Every login, configuration change, and admin
                      action — filterable + paginated.
                    </div>
                  </div>
                  <Link
                    to="/settings/audit"
                    className="text-[12.5px] text-muted hover:text-text inline-flex items-center gap-1.5"
                  >
                    Open audit log
                    <Icon name="arrow_up_right" size={12} />
                  </Link>
                </div>
              </CardBody>
            </Card>

            {/* Stage 20: VirusTotal integration is plugin-based.
                Discoverability matters more than functional editing
                here — the actual API key lives in the plugin settings
                dialog reached from the Plugins card above. This stub
                shows the operator that the integration exists, how to
                enable it, and where to find the docs. */}
            <VirusTotalCard />
          </>
        ) : null}
      </div>
    </>
  );
}

// Stage 6 audit fix (Issue 8): tab vocabulary lives next to the
// SettingsPage that uses it. Adding a tab is one entry here plus
// one branch in the render — small enough that hoisting to its
// own module isn't justified.
// v1.9 Stage 2.1: the "integrations" tab was retired; its only
// content (PathMappingsPanel) now lives on /integrations as the
// canonical home for the surface.
type SettingsTab = "workspace" | "system" | "security";

// Stage 7 (audit follow-up): System sub-tab vocabulary. Adding a
// new sub-tab is the same one-line-here / one-branch-in-render
// pattern.
type SystemSubTab = "runtime" | "secrets" | "config" | "housekeeping";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 py-2">
      <div className="text-[11.5px] font-medium text-muted">{label}</div>
      {children}
    </div>
  );
}

function ChoiceRow({ children }: { children: ReactNode }) {
  return <div className="flex gap-2 flex-wrap">{children}</div>;
}

function Choice({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="px-2.5 py-1 inline-flex items-center gap-1.5 rounded-[6px] border text-[12px] capitalize"
      style={{
        background: selected ? "var(--surface-sunk)" : "transparent",
        borderColor: selected ? "var(--border-strong)" : "var(--border)",
        color: "var(--text)",
      }}
    >
      {children}
    </button>
  );
}

// ── Libraries ────────────────────────────────────────────────
function LibrariesCard() {
  const libraries = useLibraries();
  const create = useCreateLibrary();
  const remove = useDeleteLibrary();
  const update = useUpdateLibrary();
  const scan = useTriggerScan();

  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [kind, setKind] = useState<"movies" | "tv" | "music" | "mixed">("movies");
  const [error, setError] = useState<string | null>(null);
  // Tracks which library row is currently being deleted, so the
  // delete button on every row disables while a delete is in flight.
  // Previously a rapid click on two delete buttons fired both DELETEs
  // in parallel via ``remove.mutate()``. The backend handled each one
  // fine on its own, but the API process serves scans synchronously
  // by default and any in-flight scan held the session — the second
  // DELETE could time out or interleave with the scan's transaction
  // in unexpected ways. Serializing on the client side makes the UX
  // predictable: each delete completes (including its broad query
  // invalidation) before the next can start.
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Stage 5 (audit follow-up): the row that's currently being
  // edited via LibraryEditDialog. ``null`` means "no dialog open".
  const [editing, setEditing] = useState<Library | null>(null);

  async function handleDelete(libId: string, libName: string) {
    if (deletingId !== null) return;
    if (!confirm(`Delete library "${libName}"?`)) return;
    setDeletingId(libId);
    setError(null);
    try {
      await remove.mutateAsync(libId);
    } catch (err) {
      setError(
        `Could not delete "${libName}": ${(err as Error).message}`,
      );
    } finally {
      setDeletingId(null);
    }
  }

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await create.mutateAsync({ name, root_path: rootPath, kind });
      setName("");
      setRootPath("");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Card>
      <CardHead title="Libraries" subtitle="Roots that Auditarr scans" />
      <CardBodyFlush>
        {libraries.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading libraries…" />
          </div>
        ) : libraries.data && libraries.data.length > 0 ? (
          <div>
            {libraries.data.map((lib) => (
              <div
                key={lib.id}
                className="px-4 py-3 border-b border-border last:border-b-0 flex items-center gap-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium truncate">{lib.name}</span>
                    <Pill>{lib.kind}</Pill>
                    {!lib.enabled ? <Pill>disabled</Pill> : null}
                  </div>
                  <div className="text-[11.5px] text-muted font-mono truncate">{lib.root_path}</div>
                  {lib.last_scan_at ? (
                    <div className="text-[11px] text-muted-2 mt-0.5">
                      Last scan {fmtNum(lib.last_scan_file_count ?? 0)} files ·{" "}
                      {lib.last_scan_status}
                    </div>
                  ) : null}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => update.mutate({ id: lib.id, patch: { enabled: !lib.enabled } })}
                  title={lib.enabled ? "Disable" : "Enable"}
                >
                  <Icon name={lib.enabled ? "check" : "x"} size={12} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={scan.isPending || !lib.enabled}
                  onClick={() => scan.mutate({ libraryId: lib.id })}
                  title="Run scan"
                >
                  <Icon name="play" size={12} />
                </Button>
                {/* Stage 5 (audit follow-up): Edit button opens
                    LibraryEditDialog. Pre-Stage-5 the only way to
                    change name/root_path/kind/interval was via the
                    API directly. */}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing(lib)}
                  title="Edit library"
                  aria-label={`Edit library ${lib.name}`}
                >
                  <Icon name="edit" size={12} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={deletingId !== null}
                  onClick={() => handleDelete(lib.id, lib.name)}
                  title={
                    deletingId !== null && deletingId !== lib.id
                      ? "Wait for current delete to finish"
                      : "Delete"
                  }
                >
                  <Icon name="trash" size={12} />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-4 py-6">
            <EmptyState
              icon="folder"
              title="No libraries yet"
              description="Add a library below. Auditarr will index it on the next scan."
            />
          </div>
        )}

        <form
          onSubmit={onAdd}
          className="px-4 py-3 border-t border-border flex flex-wrap items-end gap-3 bg-surface-sunk"
        >
          <label className="flex flex-col gap-1.5 flex-1 min-w-[160px]">
            <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
              Name
            </span>
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="h-8 px-2 text-[13px] bg-surface border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
              placeholder="Movies"
            />
          </label>
          <label className="flex flex-col gap-1.5 flex-[2] min-w-[260px]">
            <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
              Root path
            </span>
            <input
              required
              value={rootPath}
              onChange={(e) => setRootPath(e.target.value)}
              className="h-8 px-2 text-[13px] bg-surface border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent font-mono"
              placeholder="/data/media/movies"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
              Kind
            </span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as "movies" | "tv" | "music" | "mixed")}
              className="h-8 px-2 text-[13px] bg-surface border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
            >
              <option value="movies">Movies</option>
              <option value="tv">TV</option>
              <option value="music">Music</option>
              <option value="mixed">Mixed</option>
            </select>
          </label>
          <Button type="submit" variant="primary" size="md" disabled={create.isPending}>
            <Icon name="plus" size={12} />
            <span className="ml-1">Add library</span>
          </Button>
          {error ? <div className="basis-full text-[12px] text-sev-error">{error}</div> : null}
        </form>
      </CardBodyFlush>
      {/* Stage 5 (audit follow-up): mounted unconditionally;
          renders nothing when ``editing`` is null. */}
      <LibraryEditDialog
        library={editing}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      />
    </Card>
  );
}

// ── Stage 20: read-only system config sections ──────────────
// Renders the structured response from GET /system/config as a
// stack of cards. Admin-only — non-admin users get 403, which we
// detect via the query being in an error state, and silently hide
// the entire group. This keeps the rest of the Settings page
// (libraries, appearance) usable for everyone.

function SystemConfigCards() {
  const cfg = useSystemConfig();

  if (cfg.isLoading) {
    return (
      <Card>
        <CardHead title="System configuration" />
        <CardBody>
          <LoadingState label="Loading system config…" />
        </CardBody>
      </Card>
    );
  }

  // Non-admin → 403. We don't render the section at all rather
  // than showing an error state, because for non-admins this is
  // the expected outcome, not a fault.
  if (cfg.isError || !cfg.data) {
    return null;
  }

  return (
    <>
      <ApiSettingsCard config={cfg.data.api} />
      <AuthSettingsCard config={cfg.data.auth} />
      <ScannerSettingsCard />
      <UpdaterSettingsCard config={cfg.data.updater} />
      <StoragePathsCard config={cfg.data.storage} />
      <PluginGalleryCard config={cfg.data.plugins} />
      <HousekeepingCard config={cfg.data.housekeeping} />
    </>
  );
}

// Small helper: a key/value row with monospace value and an optional
// "secret-redacted" hint. Used by every read-only config card so they
// all render with the same look.
function ConfigRow({
  label,
  value,
  mono = true,
  hint,
}: {
  label: string;
  value: string | number | boolean | null | string[];
  mono?: boolean;
  hint?: string;
}) {
  const displayValue =
    value === null
      ? "—"
      : Array.isArray(value)
        ? value.length === 0
          ? "(none)"
          : value.join(", ")
        : typeof value === "boolean"
          ? value
            ? "true"
            : "false"
          : String(value);
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 border-b border-border last:border-b-0">
      <div className="text-[12.5px] text-text-2 shrink-0">{label}</div>
      <div className="min-w-0 flex-1 text-right">
        <div
          className={cn(
            "text-[12px] truncate",
            mono && "font-mono",
            value === null ? "text-muted-2" : "text-text",
          )}
          title={displayValue}
        >
          {displayValue}
        </div>
        {hint ? <div className="text-[10.5px] text-muted-2">{hint}</div> : null}
      </div>
    </div>
  );
}

// Banner the operator can't miss explaining that these settings
// require env-file edits + service restart. Shared by every
// read-only config card.
function ReadOnlyBanner({ envSection }: { envSection: string }) {
  return (
    <div className="text-[11.5px] text-muted-2 mb-2 leading-relaxed">
      Read-only. Edit <code className="font-mono text-text-2">{envSection}</code> in your env file (
      <code className="font-mono">.env</code> for Docker,{" "}
      <code className="font-mono">/etc/auditarr/auditarr.env</code> for bare-metal) and restart the
      service to change these.
    </div>
  );
}

// ── API ─────────────────────────────────────────────────────
function ApiSettingsCard({ config }: { config: SystemConfig["api"] }) {
  return (
    <Card>
      <CardHead title="API" subtitle="HTTP server + CORS + WebSocket" />
      <CardBody>
        <ReadOnlyBanner envSection="AUDITARR_HOST / AUDITARR_PORT / AUDITARR_ALLOWED_ORIGINS / ..." />
        <ConfigRow label="Bind host" value={config.host} />
        <ConfigRow label="Bind port" value={config.port} />
        <ConfigRow label="API prefix" value={config.api_prefix} />
        <ConfigRow label="API version" value={config.api_version} />
        <ConfigRow label="Environment" value={config.env} />
        <ConfigRow label="Log level" value={config.log_level} />
        <ConfigRow label="Log format" value={config.log_format} />
        <ConfigRow
          label="Allowed CORS origins"
          value={config.allowed_origins}
          hint={
            config.allowed_origins.length === 0
              ? "No browser frontend will be able to call the API cross-origin."
              : undefined
          }
        />
        <ConfigRow
          label="WebSocket auth required"
          value={config.ws_require_auth}
          hint={config.ws_require_auth ? undefined : "DEBUG ONLY — never disable in production."}
        />
      </CardBody>
    </Card>
  );
}

// ── Auth ────────────────────────────────────────────────────
function AuthSettingsCard({ config }: { config: SystemConfig["auth"] }) {
  return (
    <Card>
      <CardHead title="Auth" subtitle="Tokens + rate limits" />
      <CardBody>
        <ReadOnlyBanner envSection="AUDITARR_ACCESS_TOKEN_TTL_MINUTES / AUDITARR_AUTH_RATE_LIMIT_*" />
        <ConfigRow
          label="Access token TTL"
          value={`${config.access_token_ttl_minutes} min`}
          mono={false}
        />
        <ConfigRow
          label="Refresh token TTL"
          value={`${config.refresh_token_ttl_days} days`}
          mono={false}
        />
        <ConfigRow
          label="Login rate limit"
          value={`${config.rate_limit_attempts} attempts per ${config.rate_limit_window_seconds}s`}
          mono={false}
        />
      </CardBody>
    </Card>
  );
}

// ── Scanner ─────────────────────────────────────────────────
// Per-library scanner config lives in the Libraries card above
// (scan_interval_minutes per library). There aren't any global
// scanner-side env settings right now, so this card surfaces that
// fact explicitly rather than render an empty section.
function ScannerSettingsCard() {
  return (
    <Card>
      <CardHead title="Scanner" subtitle="ffprobe + file-tree walker" />
      <CardBody>
        <div className="text-[12.5px] text-text-2 leading-relaxed">
          Scanner configuration is <em>per-library</em> — set{" "}
          <code className="font-mono text-[11.5px]">scan_interval_minutes</code> on each library in
          the <strong>Libraries</strong> card above. The walker reuses the platform{" "}
          <code className="font-mono text-[11.5px]">ffprobe</code> binary; if it's missing or stale,
          install/upgrade it on the host. The scanner has no global tuning knobs at the
          environment-variable level — it adapts concurrency based on available CPUs at runtime.
        </div>
      </CardBody>
    </Card>
  );
}

// ── Updater ─────────────────────────────────────────────────
function UpdaterSettingsCard({ config }: { config: SystemConfig["updater"] }) {
  return (
    <Card>
      <CardHead title="Updater" subtitle="Release feed + install-mode-aware apply" />
      <CardBody>
        <ReadOnlyBanner envSection="AUDITARR_UPDATE_FEED_URL / AUDITARR_UPDATE_INSTALL_MODE / ..." />
        <ConfigRow label="Feed URL" value={config.feed_url} />
        <ConfigRow
          label="Check interval"
          value={`${config.check_interval_minutes} min`}
          mono={false}
        />
        <ConfigRow
          label="Install mode"
          value={config.install_mode}
          hint={
            config.install_mode === "unmanaged"
              ? "Auto-apply disabled — see Help & updates for the override."
              : config.install_mode === "auto"
                ? "Auto-detect on startup."
                : undefined
          }
        />
        <ConfigRow label="Apply sentinel" value={config.apply_sentinel} />
        <ConfigRow label="Apply status path" value={config.apply_status_path} />
      </CardBody>
    </Card>
  );
}

// ── Storage paths ───────────────────────────────────────────
function StoragePathsCard({ config }: { config: SystemConfig["storage"] }) {
  return (
    <Card>
      <CardHead title="Storage paths" subtitle="Database, Redis, on-disk directories" />
      <CardBody>
        <ReadOnlyBanner envSection="AUDITARR_DATABASE_URL / AUDITARR_REDIS_URL / AUDITARR_DATA_DIR / ..." />
        <ConfigRow label="Database URL" value={config.database_url} hint="Password redacted." />
        <ConfigRow
          label="Database pool size"
          value={`${config.database_pool_size} + ${config.database_max_overflow} overflow`}
          mono={false}
        />
        <ConfigRow label="Redis URL" value={config.redis_url} hint="Password redacted." />
        <ConfigRow label="Job queue name" value={config.queue_name} />
        <ConfigRow label="Data directory" value={config.data_dir} />
        <ConfigRow label="Plugin directory" value={config.plugin_dir} />
        <ConfigRow label="Built-in plugins" value={config.builtin_plugin_dir} />
        <ConfigRow label="Docs directory" value={config.docs_dir} />
        <ConfigRow
          label="Frontend dist"
          value={config.frontend_dist}
          hint={
            config.frontend_dist === null
              ? "Not set — the API doesn't serve the SPA. Run a separate frontend host or set AUDITARR_FRONTEND_DIST."
              : undefined
          }
        />
      </CardBody>
    </Card>
  );
}

// ── Plugin gallery ──────────────────────────────────────────
function PluginGalleryCard({ config }: { config: SystemConfig["plugins"] }) {
  return (
    <Card>
      <CardHead title="Plugin gallery" />
      <CardBody>
        <ReadOnlyBanner envSection="AUDITARR_PLUGIN_GALLERY_URL" />
        <ConfigRow
          label="Gallery URL"
          value={config.gallery_url}
          hint={
            config.gallery_url === ""
              ? "Gallery disabled — set the env var to point at a manifest URL."
              : undefined
          }
        />
      </CardBody>
    </Card>
  );
}

// ── Housekeeping ────────────────────────────────────────────
function HousekeepingCard({ config }: { config: SystemConfig["housekeeping"] }) {
  return (
    <Card>
      <CardHead title="Housekeeping" subtitle="Audit-table retention windows" />
      <CardBody>
        <ReadOnlyBanner envSection="AUDITARR_HOUSEKEEPING_*_RETENTION_DAYS" />
        <ConfigRow
          label="Notification deliveries"
          value={
            config.delivery_retention_days === 0
              ? "kept indefinitely"
              : `${config.delivery_retention_days} days`
          }
          mono={false}
        />
        <ConfigRow
          label="Update feed checks"
          value={
            config.update_check_retention_days === 0
              ? "kept indefinitely"
              : `${config.update_check_retention_days} days`
          }
          mono={false}
        />
        <ConfigRow
          label="Rule evaluations"
          value={
            config.rule_evaluation_retention_days === 0
              ? "kept indefinitely"
              : `${config.rule_evaluation_retention_days} days`
          }
          mono={false}
        />
        <ConfigRow
          label="Job runs"
          value={
            config.job_run_retention_days === 0
              ? "kept indefinitely"
              : `${config.job_run_retention_days} days`
          }
          mono={false}
        />
      </CardBody>
    </Card>
  );
}

// ── VirusTotal integration discovery card ───────────────────
// Doesn't ship a fully-wired VirusTotal scanner — that's a backend
// integration with quota management, scan caching, and rescan
// scheduling that deserves its own multi-stage build. What we ship
// here is the discovery surface: the operator sees that VirusTotal
// is available, knows where to configure it once we ship the
// plugin, and gets pointed at the docs in the meantime.
//
// When the VirusTotal plugin lands, this card will be replaced
// with a real plugin_settings-backed editor.
function VirusTotalCard() {
  return (
    <Card>
      <CardHead
        title="VirusTotal"
        subtitle="Hash-based threat lookups for downloaded media"
        actions={<Pill>preview</Pill>}
      />
      <CardBody>
        <div className="text-[12.5px] text-text-2 leading-relaxed">
          VirusTotal integration is in preview. When enabled, the scanner submits SHA256 hashes of
          new media files to VirusTotal's public API and surfaces non-clean verdicts as
          severity-error issues on the Files page.
        </div>
        <div className="text-[11.5px] text-muted-2 mt-3 leading-relaxed">
          Configuration will live under the <strong>Plugins</strong> card above once the{" "}
          <code className="font-mono">virustotal</code> plugin ships. Until then, hash submission is
          disabled and no data leaves your network.
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11.5px]">
          <Pill className="text-muted-2 border-border">
            <Icon name="alert" size={11} className="mr-1" /> Disabled
          </Pill>
          <Pill className="text-muted-2 border-border">
            <Icon name="settings" size={11} className="mr-1" /> API key not configured
          </Pill>
          <Pill className="text-muted-2 border-border">
            <Icon name="clock" size={11} className="mr-1" /> 0 hashes submitted
          </Pill>
        </div>
      </CardBody>
    </Card>
  );
}

// ── Stage 7: account security entry-point ──────────────────────
//
// A thin discoverability card. Pre-Stage-7 the only path to the
// account page was the avatar button in TopNav — operators who
// don't think to click their initials never found the password
// and sessions surfaces. This card lives in Settings → Security
// next to the VirusTotal stub so account security is grouped
// with the rest of the security surface.
//
// We deliberately don't duplicate the forms — they live in
// ``AccountPage`` and any change there benefits both surfaces.

function AccountSecurityCard() {
  return (
    <Card>
      <CardHead
        title="Account security"
        subtitle="Profile · password · active sessions"
      />
      <CardBody>
        <p className="text-[12.5px] text-muted-2 mb-3 max-w-xl">
          Your profile (display name, email), password change, and
          active-session controls live on the dedicated{" "}
          <a
            href="/account"
            className="text-accent hover:underline font-medium"
          >
            Account page
          </a>
          . The same surface is reachable any time from the avatar
          button in the top navigation.
        </p>
        <a
          href="/account"
          className={cn(
            "inline-flex items-center gap-1.5 px-2.5 py-1.5",
            "border border-border rounded-md text-[12.5px]",
            "hover:bg-[var(--hover)] transition-colors",
          )}
        >
          <Icon name="user" size={12} />
          Open account settings
        </a>
      </CardBody>
    </Card>
  );
}

// ── Stage 7: path-mappings discoverability ─────────────────────
//
// The full editor lives in Settings → Integrations. This card is
// a one-screen summary for the Workspace tab so an operator who
// only ever opens Workspace can still find it. Clicking "Open"
// switches the outer tab via the ``onJump`` callback rather than
// navigating, so the operator stays inside the Settings page
// (preserves any unsaved edits in other panels).

function PathMappingsSummaryCard() {
  return (
    <Card>
      <CardHead
        title="Path mappings"
        subtitle="How integrations' paths translate to Auditarr's view"
      />
      <CardBody>
        <p className="text-[12.5px] text-muted-2 mb-3 max-w-xl">
          When an integration (Plex, Sonarr, etc.) reports a file
          path that differs from how Auditarr sees the same file,
          a path mapping rewrites it during resolution. The full
          editor — including the global-mapping layer — lives on
          the Integrations page.
        </p>
        <Link
          to="/integrations"
          className="inline-flex items-center gap-1.5 px-3 h-8 rounded-[6px] text-[12.5px] border border-border bg-surface-2 text-text-2 hover:bg-[var(--hover)] transition-colors"
          aria-label="Open Path mappings on the Integrations page"
        >
          <Icon name="folder" size={12} />
          <span>Open Path mappings</span>
        </Link>
      </CardBody>
    </Card>
  );
}
