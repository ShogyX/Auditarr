/**
 * Stage 12 audit fix (Issue 17) — Updater panel.
 *
 * Extracted from ``HelpPage`` (where it had been a nested helper).
 * Now consumed by ``ChangelogPage`` — the natural home for "what
 * version am I running, what's available, what changed". Behavior
 * is unchanged from the original; only the file location moved.
 */

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import { ErrorState, LoadingState } from "@/components/ui/States";
import {
  useForceClearApply,
  useRequestApply,
  useRollback,
  useTriggerCheck,
  useUpdateApplies,
  useUpdaterStatus,
  type UpdateApply,
} from "@/hooks/useUpdater";

// v1.9 Stage 1.2 — when an apply has been ``requested`` or
// ``running`` for longer than this, the UI surfaces a manual
// "force-clear" button. The backend's authoritative reaper
// runs every poll on a longer window (default 30 min), so this
// hint is the friendly mid-window option for operators who
// don't want to wait.
const STUCK_APPLY_THRESHOLD_MS = 5 * 60 * 1000;

function applyAppearsStuck(apply: UpdateApply): boolean {
  if (apply.status !== "requested" && apply.status !== "running") return false;
  const started = Date.parse(apply.started_at);
  if (!Number.isFinite(started)) return false;
  return Date.now() - started > STUCK_APPLY_THRESHOLD_MS;
}

export function UpdaterPanel() {
  const status = useUpdaterStatus();
  const applies = useUpdateApplies(5);
  const triggerCheck = useTriggerCheck();
  const requestApply = useRequestApply();
  const rollback = useRollback();
  const forceClear = useForceClearApply();

  if (status.isLoading) {
    return (
      <Card>
        <CardBody>
          <LoadingState label="Checking for updates…" />
        </CardBody>
      </Card>
    );
  }
  if (status.isError || !status.data) {
    return (
      <Card>
        <CardBody>
          <ErrorState
            title="Failed to load update status"
            description={(status.error as Error)?.message}
          />
        </CardBody>
      </Card>
    );
  }

  const s = status.data;
  const hasUpdate = s.has_update && s.latest_version;

  // Stage 19: install-mode aware copy.
  const installModeLabel: Record<typeof s.install_mode, string> = {
    docker: "Docker",
    "bare-metal": "Bare-metal (systemd)",
    unmanaged: "Unmanaged",
  };
  const applyButtonText = (() => {
    if (s.apply_in_progress) return "Apply in progress…";
    if (!s.latest_version) return "Apply";
    if (s.install_mode === "bare-metal") {
      return `Apply ${s.latest_version} (systemd)`;
    }
    return `Apply ${s.latest_version}`;
  })();

  // v1.9.1 Stage 1.6 — Docker installs surface the manual host
  // commands instead of an Apply button. Containers can't recreate
  // themselves without holding the docker socket, which defeats
  // isolation, so the operator drives the upgrade from the host.
  const showDockerManualBlock = s.install_mode === "docker";

  return (
    <Card>
      <CardHead
        title="Updates"
        subtitle={`Installed: ${s.installed_version} · ${installModeLabel[s.install_mode]}`}
        actions={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => triggerCheck.mutate()}
            disabled={triggerCheck.isPending}
            title="Force a feed check"
          >
            <Icon name="refresh" size={12} />
            <span className="ml-1">{triggerCheck.isPending ? "Checking…" : "Check now"}</span>
          </Button>
        }
      />
      <CardBody>
        <div className="flex flex-col gap-3">
          {/* v1.9.1 Stage 1.6 — Docker installs get install-mode-specific
              copy with the host commands inline; unmanaged keeps the
              generic warning. */}
          {showDockerManualBlock ? (
            <div className="text-[12px] p-2.5 rounded-md bg-sev-info/10 text-sev-info border border-sev-info/30">
              <Icon name="info" size={11} className="inline mr-1" />
              <span className="font-semibold">Docker install.</span> Auditarr can't recreate
              its own container — update by running these commands on the host:
            </div>
          ) : !s.apply_enabled ? (
            <div className="text-[12px] p-2.5 rounded-md bg-sev-warn/10 text-sev-warn border border-sev-warn/30">
              <Icon name="alert" size={11} className="inline mr-1" />
              Auto-apply is disabled — install environment is{" "}
              <code className="font-mono">{s.install_mode}</code>. Set{" "}
              <code className="font-mono">AUDITARR_UPDATE_INSTALL_MODE</code> in your config
              to <code className="font-mono">bare-metal</code> to enable in-UI apply, or
              update Auditarr by hand.
            </div>
          ) : null}

          {showDockerManualBlock && s.manual_apply_command ? (
            <ManualCommandBlock command={s.manual_apply_command} />
          ) : null}

          <div className="flex items-center gap-3 flex-wrap">
            {hasUpdate ? (
              <>
                <Pill className="text-sev-info border-sev-info/40 bg-sev-info/10">
                  Update available
                </Pill>
                <Tag>{s.latest_version}</Tag>
                {showDockerManualBlock ? null : (
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => s.latest_version && requestApply.mutate(s.latest_version)}
                    disabled={requestApply.isPending || s.apply_in_progress || !s.apply_enabled}
                    title={
                      !s.apply_enabled
                        ? "Apply disabled — install environment isn't auto-update-capable"
                        : undefined
                    }
                  >
                    <Icon name="download" size={12} />
                    <span className="ml-1">{applyButtonText}</span>
                  </Button>
                )}
              </>
            ) : (
              <Pill className="text-sev-ok border-sev-ok/40 bg-sev-ok/10">Up to date</Pill>
            )}
            {s.last_checked_at ? (
              <span className="text-[11.5px] text-muted-2">
                Last checked {new Date(s.last_checked_at).toLocaleString()}
                {s.last_check_ok === false ? " (failed)" : ""}
              </span>
            ) : (
              <span className="text-[11.5px] text-muted-2">Never checked</span>
            )}
          </div>
          {s.last_check_ok === false && s.last_check_detail ? (
            <div className="text-[12px] text-sev-error">Feed: {s.last_check_detail}</div>
          ) : null}
          {applies.data && applies.data.length > 0 ? (
            <div className="border-t border-border pt-3 -mb-1">
              <div className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold mb-2">
                Recent applies
              </div>
              <div className="flex flex-col gap-1">
                {applies.data.map((apply) => (
                  <ApplyRow
                    key={apply.id}
                    apply={apply}
                    onRollback={() => rollback.mutate(apply.id)}
                    onForceClear={() => forceClear.mutate(apply.id)}
                    forceClearPending={forceClear.isPending}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </CardBody>
    </Card>
  );
}

function ApplyRow({
  apply,
  onRollback,
  onForceClear,
  forceClearPending,
}: {
  apply: UpdateApply;
  onRollback: () => void;
  onForceClear: () => void;
  forceClearPending: boolean;
}) {
  // v1.9 Stage 1.2 — show "Force-clear" on rows that have been
  // open for longer than the stuck threshold. The backend's
  // reaper will eventually catch this row on its own, but
  // surfacing the affordance lets the operator unblock the
  // next apply request immediately.
  const stuck = applyAppearsStuck(apply);
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <Pill className={applyStatusClass(apply.status)}>{apply.status}</Pill>
      <span className="font-mono">
        {apply.from_version ?? "?"} → {apply.to_version}
      </span>
      <span className="text-muted-2">{new Date(apply.started_at).toLocaleString()}</span>
      {apply.error ? (
        <span className="text-sev-error truncate flex-1">{apply.error}</span>
      ) : apply.detail ? (
        <span className="text-muted-2 truncate flex-1">{apply.detail}</span>
      ) : null}
      {stuck ? (
        <button
          onClick={onForceClear}
          disabled={forceClearPending}
          className="ml-auto text-[11.5px] text-sev-warn hover:text-sev-error underline disabled:opacity-50"
          title="This apply has been open for over 5 minutes. Force-clearing marks it failed so the next apply can run."
        >
          {forceClearPending ? "Clearing…" : "Force-clear"}
        </button>
      ) : apply.status === "completed" && apply.from_version ? (
        <button
          onClick={onRollback}
          className="ml-auto text-[11.5px] text-muted-2 hover:text-text underline"
          title="Roll back to the previous version"
        >
          Roll back
        </button>
      ) : null}
    </div>
  );
}

function applyStatusClass(status: string): string {
  switch (status) {
    case "completed":
      return "text-sev-ok border-sev-ok/40 bg-sev-ok/10";
    case "requested":
    case "running":
      return "text-sev-info border-sev-info/40 bg-sev-info/10";
    case "failed":
      return "text-sev-error border-sev-error/40 bg-sev-error/10";
    case "rolled_back":
      return "text-muted-2 border-border bg-surface-2";
    default:
      return "";
  }
}

// v1.9.1 Stage 1.6 — Docker manual-apply command block with a
// copy-to-clipboard affordance. The command string is rendered as-is
// from the API so docs and UI can't drift.
function ManualCommandBlock({ command }: { command: string }) {
  const onCopy = () => {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(command).catch(() => {
        // Clipboard access blocked (e.g. insecure context).
        // Selection-based copy works as a fallback in any browser.
      });
    }
  };
  return (
    <div className="relative rounded-md bg-surface-2 border border-border">
      <button
        onClick={onCopy}
        className="absolute top-1.5 right-1.5 text-[10.5px] text-muted-2 hover:text-text px-1.5 py-0.5 rounded border border-border bg-surface-1"
        title="Copy command to clipboard"
      >
        <Icon name="duplicate" size={10} className="inline mr-1" />
        Copy
      </button>
      <pre className="text-[11.5px] font-mono p-2.5 pr-14 overflow-x-auto whitespace-pre">
        {command}
      </pre>
    </div>
  );
}
