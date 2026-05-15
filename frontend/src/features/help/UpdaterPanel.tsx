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
  useRequestApply,
  useRollback,
  useTriggerCheck,
  useUpdateApplies,
  useUpdaterStatus,
  type UpdateApply,
} from "@/hooks/useUpdater";

export function UpdaterPanel() {
  const status = useUpdaterStatus();
  const applies = useUpdateApplies(5);
  const triggerCheck = useTriggerCheck();
  const requestApply = useRequestApply();
  const rollback = useRollback();

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
    if (s.install_mode === "docker") {
      return `Apply ${s.latest_version} (Docker)`;
    }
    if (s.install_mode === "bare-metal") {
      return `Apply ${s.latest_version} (systemd)`;
    }
    return `Apply ${s.latest_version}`;
  })();

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
          {/* Stage 19: warn the operator when apply is disabled. */}
          {!s.apply_enabled ? (
            <div className="text-[12px] p-2.5 rounded-md bg-sev-warn/10 text-sev-warn border border-sev-warn/30">
              <Icon name="alert" size={11} className="inline mr-1" />
              Auto-apply is disabled — install environment is{" "}
              <code className="font-mono">{s.install_mode}</code>. Set{" "}
              <code className="font-mono">AUDITARR_UPDATE_INSTALL_MODE</code> in your config to{" "}
              <code className="font-mono">docker</code> or{" "}
              <code className="font-mono">bare-metal</code> (and install the matching helper
              script), or update Auditarr by hand.
            </div>
          ) : null}

          <div className="flex items-center gap-3 flex-wrap">
            {hasUpdate ? (
              <>
                <Pill className="text-sev-info border-sev-info/40 bg-sev-info/10">
                  Update available
                </Pill>
                <Tag>{s.latest_version}</Tag>
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

function ApplyRow({ apply, onRollback }: { apply: UpdateApply; onRollback: () => void }) {
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
      {apply.status === "completed" && apply.from_version ? (
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
