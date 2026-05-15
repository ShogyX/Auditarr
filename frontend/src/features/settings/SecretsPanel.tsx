/**
 * Encrypted-secrets editor (Stage 22).
 *
 * Renders one card per secret slot. The plaintext NEVER round-trips —
 * the backend's :class:`SecretService.list_status` returns only
 * metadata (``has_value``, audit timestamps, last test outcome). The
 * input is one-way: operators paste a value, click Save, the backend
 * encrypts it before storage, and the panel re-reads only the status.
 *
 * Test handler:
 *
 *   For slots with ``has_test_handler``, a "Test connection" button
 *   probes the upstream API with the stored ciphertext (decrypted in
 *   the service layer, never leaves the process). The result is
 *   recorded as audit metadata (``last_tested_at``, ``last_test_ok``,
 *   ``last_test_detail``) and toasted to the operator.
 *
 *   We do not throw when the upstream rejects the secret; the hook
 *   converts the 502 into ``{ ok: false }`` so the panel can render
 *   "test failed: <reason>" inline rather than blowing up.
 *
 * Clear:
 *
 *   The Clear button deletes the row entirely (DELETE), so the panel
 *   reflects ``has_value: false`` after. We confirm before clearing
 *   because there's no undo — the operator has to paste the secret
 *   again to recover.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, LoadingState } from "@/components/ui/States";
import { cn } from "@/lib/cn";
import { toast } from "@/lib/toast";
import {
  ApiError,
  useClearSecret,
  useSecrets,
  useSetSecret,
  useTestSecret,
  type SecretRow,
} from "@/hooks/useRuntimeSettings";

export function SecretsPanel() {
  const { secrets, isLoading, isForbidden } = useSecrets();

  if (isLoading) {
    return (
      <Card>
        <CardHead title="Secrets" subtitle="Loading…" />
        <div className="p-6">
          <LoadingState label="Loading secrets…" />
        </div>
      </Card>
    );
  }
  if (isForbidden) {
    return (
      <Card>
        <CardHead title="Secrets" />
        <div className="p-6">
          <EmptyState
            icon="lock"
            title="Admin access required"
            description="Encrypted secrets are admin-only."
          />
        </div>
      </Card>
    );
  }
  if (secrets.length === 0) {
    return (
      <Card>
        <CardHead title="Secrets" />
        <div className="p-6">
          <EmptyState
            icon="lock"
            title="No managed secrets"
            description="No secret slots are declared in the runtime schema."
          />
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHead
        title="Secrets"
        subtitle="Encrypted at rest · never returned in API responses"
      />
      <div className="p-4 flex flex-col gap-3">
        {secrets.map((s) => (
          <SecretRowCard key={s.key} secret={s} />
        ))}
      </div>
    </Card>
  );
}

function SecretRowCard({ secret }: { secret: SecretRow }) {
  const setSecret = useSetSecret();
  const clearSecret = useClearSecret();
  const testSecret = useTestSecret();
  const [draft, setDraft] = useState("");

  const draftTooShort = draft.length > 0 && draft.length < secret.min_length;
  const draftTooLong = draft.length > secret.max_length;

  async function onSave() {
    if (!draft || draftTooShort || draftTooLong) return;
    try {
      await setSecret.mutateAsync({ key: secret.key, plaintext: draft });
      setDraft("");
      toast(secret.has_value ? "Secret replaced" : "Secret saved", "ok");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Could not save ${secret.label}: ${msg}`, "error", 5000);
    }
  }

  async function onClear() {
    if (!confirm(`Clear ${secret.label}? You'll need to paste it again to restore.`)) {
      return;
    }
    try {
      await clearSecret.mutateAsync(secret.key);
      toast("Secret cleared", "ok");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Could not clear ${secret.label}: ${msg}`, "error", 5000);
    }
  }

  async function onTest() {
    try {
      const result = await testSecret.mutateAsync(secret.key);
      if (result.ok) {
        toast(`${secret.label}: ${result.detail || "ok"}`, "ok");
      } else {
        toast(`${secret.label}: ${result.detail || "failed"}`, "warn", 5000);
      }
    } catch (err) {
      // Either no-secret-stored or some other unexpected condition.
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Test failed for ${secret.label}: ${msg}`, "error", 5000);
    }
  }

  const lastTestedAt = secret.last_tested_at
    ? new Date(secret.last_tested_at)
    : null;
  const lastSetAt = secret.last_set_at ? new Date(secret.last_set_at) : null;

  return (
    <div className="secret-card">
      <div className="secret-card-head">
        <Icon name="lock" size={14} className="text-muted-2" />
        <span className="text-[13.5px] font-medium">{secret.label}</span>
        <code className="font-mono text-[11.5px] text-muted-2">{secret.key}</code>
        <span className="flex-1" />
        {secret.has_value ? (
          <Pill sev="ok">set</Pill>
        ) : (
          <Pill>not set</Pill>
        )}
      </div>

      <p className="text-[12.5px] text-muted leading-relaxed m-0">
        {secret.description}
      </p>

      <div className="secret-card-controls">
        <input
          type="password"
          className="settings-input mono flex-1 min-w-[240px]"
          autoComplete="off"
          placeholder={
            secret.has_value
              ? "•••••••••• (paste new value to replace)"
              : "Paste secret value"
          }
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={secret.max_length + 1 /* let the validator speak */}
          aria-invalid={draftTooShort || draftTooLong || undefined}
        />
        <Button
          size="sm"
          variant="accent"
          onClick={onSave}
          disabled={
            !draft || draftTooShort || draftTooLong || setSecret.isPending
          }
        >
          {setSecret.isPending
            ? "Saving…"
            : secret.has_value
              ? "Replace"
              : "Save secret"}
        </Button>
        {secret.has_value && secret.has_test_handler ? (
          <Button
            size="sm"
            onClick={onTest}
            disabled={testSecret.isPending}
          >
            {testSecret.isPending ? "Testing…" : "Test connection"}
          </Button>
        ) : null}
        {secret.has_value ? (
          <Button
            size="sm"
            variant="danger"
            onClick={onClear}
            disabled={clearSecret.isPending}
          >
            <Icon name="trash" size={12} /> Clear
          </Button>
        ) : null}
      </div>

      {draftTooShort || draftTooLong ? (
        <div className="runtime-warn">
          <Icon name="alert" size={14} className="text-sev-warn shrink-0 mt-0.5" />
          <span>
            Length must be between {secret.min_length} and {secret.max_length}{" "}
            characters (got {draft.length}).
          </span>
        </div>
      ) : null}

      <div className="secret-card-meta">
        {lastSetAt ? (
          <span>
            last set <code className="font-mono">{fmtAgo(lastSetAt)}</code>
          </span>
        ) : null}
        {lastTestedAt ? (
          <span className="inline-flex items-center gap-1">
            last test
            <span
              className={cn(
                "dot",
                secret.last_test_ok ? "sev-ok" : "sev-error",
              )}
            />
            <code className="font-mono">{fmtAgo(lastTestedAt)}</code>
            <span className="text-muted-2">
              · {secret.last_test_ok ? "ok" : "failed"}
              {secret.last_test_detail && !secret.last_test_ok
                ? ` — ${secret.last_test_detail}`
                : null}
            </span>
          </span>
        ) : null}
      </div>
    </div>
  );
}

// Format a relative timestamp. Uses the most legible unit for the
// magnitude — "5m", "3h", "2d" — and falls back to the raw ISO date
// for things older than a month, since "31d ago" is less useful than
// the literal "2026-04-10".
function fmtAgo(dt: Date): string {
  const ms = Date.now() - dt.getTime();
  if (ms < 0) return dt.toISOString().slice(0, 10);
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return dt.toISOString().slice(0, 10);
}
