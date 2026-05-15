/**
 * Stage 2 — Runtime settings panel (slim orchestrator).
 *
 * Composes:
 *   - ``RuntimeCategoryRail``      — left navigation
 *   - ``RuntimeFieldRow``          — per-field card
 *   - ``RuntimeSaveBar``           — pending changes / Apply
 *   - ``RuntimeConfirmApplyDialog``— diff + confirm (Stage 1 Modal)
 *   - ``RuntimeHistoryDrawer``     — NEW Stage 2 per-key audit log
 *
 * Pre-Stage-2: 614 LOC monolith
 * Post-Stage-2: ~170 LOC orchestrator + 6 focused sub-modules
 *
 * Dirty-state model (unchanged):
 *   ``edits`` is a ``{key: proposed_value}`` map. A field is "dirty"
 *   if it appears in ``edits`` AND its proposed value differs from
 *   the server-known value. Server state is never optimistically
 *   mutated — the only path to a server-side change is Apply.
 *
 * Apply flow (unchanged structurally; Stage 2 adds elevated-confirm):
 *   Apply → ConfirmApplyDialog (diff + warnings + elevated gate) →
 *   sequential PUT/DELETE per dirty field → toast.
 *
 * Stage 2 additions:
 *   - Each field card has a "history" button that opens the drawer
 *     for that field.
 *   - The confirm dialog enforces a re-type-to-confirm gate when any
 *     pending change targets an elevated-sensitivity field.
 *   - "Restart required" and "elevated" pills render on the field
 *     head when the spec sets them.
 */

import { useMemo, useState } from "react";

import { Card, CardHead } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  ApiError,
  useClearRuntimeOverride,
  useRuntimeSettings,
  useSetRuntimeOverride,
  type RuntimeField,
} from "@/hooks/useRuntimeSettings";
import { toast } from "@/lib/toast";

import { RuntimeCategoryRail } from "./RuntimeCategoryRail";
import { RuntimeConfirmApplyDialog } from "./RuntimeConfirmApplyDialog";
import { RuntimeFieldRow } from "./RuntimeFieldRow";
import { RuntimeHistoryDrawer } from "./RuntimeHistoryDrawer";
import { RuntimeSaveBar } from "./RuntimeSaveBar";
import { sameValue, type EditValue, type Edits } from "./runtimeSettingsShared";

export interface RuntimeSettingsPanelProps {
  /**
   * Stage 7 (audit follow-up): when supplied, the panel is scoped
   * to exactly one category and the left rail is hidden. The
   * Settings page uses this to mount a "Housekeeping" sub-tab
   * that shows only the housekeeping runtime fields — same code
   * path, just a thin wrapper around the existing panel.
   *
   * When omitted (the default), the panel behaves exactly as it
   * did pre-Stage-7: shows the rail and locks onto the first
   * category until the operator picks another.
   */
  categoryFilter?: string;
}

export function RuntimeSettingsPanel({
  categoryFilter,
}: RuntimeSettingsPanelProps = {}) {
  const rs = useRuntimeSettings();
  const setOverride = useSetRuntimeOverride();
  const clearOverride = useClearRuntimeOverride();

  const [activeCat, setActiveCat] = useState<string | null>(null);
  const [edits, setEdits] = useState<Edits>({});
  const [confirming, setConfirming] = useState(false);
  // Stage 2: history drawer state.
  const [historyField, setHistoryField] = useState<RuntimeField | null>(null);
  // Applied keys carry a "applied" highlight for a few seconds after
  // a successful save, so the operator gets visual confirmation.
  const [applied, setApplied] = useState<Set<string>>(new Set());

  // Lock onto the first category once we have one.
  // Stage 7 (audit follow-up): when ``categoryFilter`` is set, the
  // panel is scoped to that one category regardless of operator
  // interaction — the rail is hidden below.
  const effectiveCat = categoryFilter ?? activeCat ?? rs.categories[0]?.key ?? null;

  const fieldsInCat = useMemo(() => {
    if (!effectiveCat) return [] as RuntimeField[];
    return rs.fields.filter((f) => f.category === effectiveCat);
  }, [rs.fields, effectiveCat]);

  const dirtyKeys = useMemo(
    () =>
      Object.keys(edits).filter((k) => {
        const f = rs.fields.find((x) => x.key === k);
        if (!f) return false;
        return !sameValue(edits[k], f.value);
      }),
    [edits, rs.fields],
  );

  const dirtyByImpact = useMemo(() => {
    let immediate = 0;
    let nextTick = 0;
    for (const k of dirtyKeys) {
      const f = rs.fields.find((x) => x.key === k);
      if (!f) continue;
      if (f.impact === "immediate") immediate++;
      else nextTick++;
    }
    return { immediate, nextTick };
  }, [dirtyKeys, rs.fields]);

  // ── Edit operations ──────────────────────────────────────────
  function setVal(key: string, v: EditValue) {
    setEdits((prev) => ({ ...prev, [key]: v }));
  }

  function revertOne(key: string) {
    setEdits(({ [key]: _drop, ...rest }) => rest);
  }

  function discardAll() {
    setEdits({});
  }

  // ── Apply ────────────────────────────────────────────────────
  async function applyAll() {
    setConfirming(false);
    const ok: string[] = [];
    const fail: { key: string; err: string }[] = [];
    for (const key of dirtyKeys) {
      const f = rs.fields.find((x) => x.key === key);
      if (!f) continue;
      const target = edits[key];
      // If the target equals the env default and the field is
      // currently an override, do a DELETE rather than a PUT. This
      // keeps the override table small and matches the "restore
      // default" semantics in the prototype.
      const goingToDefault =
        sameValue(target, f.env_default) && f.is_override;
      try {
        if (goingToDefault) {
          await clearOverride.mutateAsync(key);
        } else {
          await setOverride.mutateAsync({ key, value: target });
        }
        ok.push(key);
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : String(err);
        fail.push({ key, err: msg });
      }
    }
    if (ok.length > 0) {
      setEdits((prev) => {
        const next = { ...prev };
        for (const k of ok) delete next[k];
        return next;
      });
      setApplied((prev) => {
        const next = new Set(prev);
        for (const k of ok) next.add(k);
        return next;
      });
      setTimeout(() => {
        setApplied((prev) => {
          const next = new Set(prev);
          for (const k of ok) next.delete(k);
          return next;
        });
      }, 2500);
      toast(
        `Applied ${ok.length} setting${ok.length === 1 ? "" : "s"}`,
        "ok",
      );
    }
    if (fail.length > 0) {
      const head = fail[0]!;
      toast(
        fail.length === 1
          ? `Failed to apply ${head.key}: ${head.err}`
          : `${fail.length} settings failed to apply (first: ${head.err})`,
        "error",
        5000,
      );
    }
  }

  // ── Render ───────────────────────────────────────────────────
  if (rs.isLoading) {
    return (
      <Card>
        <CardHead title="Runtime settings" subtitle="Loading…" />
        <div className="p-6">
          <LoadingState label="Loading runtime settings…" />
        </div>
      </Card>
    );
  }
  if (rs.isForbidden) {
    return (
      <Card>
        <CardHead title="Runtime settings" />
        <div className="p-6">
          <EmptyState
            icon="lock"
            title="Admin access required"
            description="Runtime settings are admin-only. Sign in with an admin account to view and edit them."
          />
        </div>
      </Card>
    );
  }
  if (rs.isError || rs.fields.length === 0) {
    return (
      <Card>
        <CardHead title="Runtime settings" />
        <div className="p-6">
          {rs.isError ? (
            <ErrorState
              title="Could not load runtime settings"
              description="The describe endpoint did not return a schema. Refresh to retry."
            />
          ) : (
            <EmptyState
              icon="cog"
              title="No runtime settings"
              description="No schema entries returned by the backend."
            />
          )}
        </div>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHead
          title="Runtime settings"
          subtitle="Schema-driven · DB-backed · audited"
        />
        <div className="runtime-grid">
          {/* Stage 7 (audit follow-up): hide the category rail when
              the panel is scoped to a single category — the rail
              would render a single useless row. */}
          {categoryFilter ? null : (
            <RuntimeCategoryRail
              categories={rs.categories}
              activeKey={effectiveCat}
              onSelect={setActiveCat}
              fields={rs.fields}
              dirtyKeys={dirtyKeys}
            />
          )}
          <div className="runtime-fields">
            {fieldsInCat.map((f) => (
              <RuntimeFieldRow
                key={f.key}
                field={f}
                proposed={edits[f.key]}
                isApplied={applied.has(f.key)}
                onChange={(v) => setVal(f.key, v)}
                onRevert={() => revertOne(f.key)}
                onRestoreDefault={() =>
                  setVal(f.key, f.env_default as EditValue)
                }
                onOpenHistory={() => setHistoryField(f)}
              />
            ))}
          </div>
        </div>

        <RuntimeSaveBar
          pendingCount={dirtyKeys.length}
          immediateCount={dirtyByImpact.immediate}
          nextTickCount={dirtyByImpact.nextTick}
          onDiscardAll={discardAll}
          onApply={() => setConfirming(true)}
          busy={setOverride.isPending || clearOverride.isPending}
        />
      </Card>

      {confirming ? (
        <RuntimeConfirmApplyDialog
          dirtyKeys={dirtyKeys}
          edits={edits}
          fields={rs.fields}
          onCancel={() => setConfirming(false)}
          onConfirm={applyAll}
          busy={setOverride.isPending || clearOverride.isPending}
        />
      ) : null}

      {/* Stage 2: per-field history drawer. Always mounted so the
          drawer animations have a consistent component lifecycle;
          ``field=null`` keeps the drawer closed. */}
      <RuntimeHistoryDrawer
        field={historyField}
        onClose={() => setHistoryField(null)}
      />
    </>
  );
}
