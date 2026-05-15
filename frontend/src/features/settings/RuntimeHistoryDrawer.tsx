/**
 * Stage 2 — Runtime setting history drawer.
 *
 * NEW for Stage 2. Reads the per-key change log from
 * ``GET /system/runtime-settings/{key}/history``, renders newest-
 * first with previous → next value transitions, the operator id,
 * and a localized timestamp.
 *
 * Built on the Stage 1 ``Drawer`` primitive so it shares the slide-
 * in animation, focus management, and Escape-to-close behavior with
 * the rest of the shell. Empty state renders for fields that have
 * never been overridden (or whose history was just cleared by an
 * operator restoring the default — the audit row is still there,
 * just with ``next_value: null``).
 */

import { Button } from "@/components/ui/Button";
import { Drawer, DrawerBody, DrawerHead } from "@/components/ui/Drawer";
import { Pill } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import {
  useRuntimeSettingHistory,
  type RuntimeField,
} from "@/hooks/useRuntimeSettings";

export interface RuntimeHistoryDrawerProps {
  /** Field whose history is being shown. ``null`` closes the drawer. */
  field: RuntimeField | null;
  onClose: () => void;
}

export function RuntimeHistoryDrawer({
  field,
  onClose,
}: RuntimeHistoryDrawerProps) {
  // The hook is unconditional but gated on ``field !== null`` via its
  // ``enabled`` flag — no network traffic when closed.
  const history = useRuntimeSettingHistory(field?.key ?? null, 50);

  return (
    <Drawer
      open={!!field}
      onOpenChange={(o) => !o && onClose()}
      ariaLabel={
        field ? `Change history for ${field.label}` : "Change history"
      }
    >
      <DrawerHead
        title="Change history"
        subtitle={field?.label ?? undefined}
        onClose={onClose}
      />
      <DrawerBody className="p-0">
        {!field ? null : history.isLoading ? (
          <div className="p-6">
            <LoadingState label="Loading history…" />
          </div>
        ) : history.isForbidden ? (
          <div className="p-6">
            <EmptyState
              icon="lock"
              title="Admin access required"
              description="Runtime-setting history is admin-only."
            />
          </div>
        ) : history.isError ? (
          <div className="p-6">
            <ErrorState
              title="Could not load history"
              description="The history endpoint failed. Refresh to retry."
            />
          </div>
        ) : history.changes.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="clock"
              title="No changes yet"
              description={`No overrides have ever been applied to ${field.label}.`}
            />
          </div>
        ) : (
          <ul className="m-0 p-0 list-none">
            {history.changes.map((change) => (
              <li
                key={change.id}
                className="px-4 py-3 border-b border-border last:border-b-0 flex flex-col gap-1"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <code className="font-mono text-[12px] text-muted-2">
                    {formatValue(change.prev_value)}
                  </code>
                  <span className="text-muted-2">→</span>
                  <code className="font-mono text-[12px] font-semibold">
                    {formatValue(change.next_value)}
                  </code>
                  {change.next_value === null && change.prev_value !== null ? (
                    <Pill sev="info" title="Cleared back to env default">
                      cleared
                    </Pill>
                  ) : null}
                </div>
                <div className="text-[11.5px] text-muted-2">
                  {new Date(change.set_at).toLocaleString()}
                  {change.set_by_user_id
                    ? ` · by ${formatOperator(change.set_by_user_id)}`
                    : " · (no operator recorded)"}
                </div>
              </li>
            ))}
          </ul>
        )}
      </DrawerBody>
      <div className="px-4 py-3 border-t border-border flex">
        <span className="flex-1" />
        <Button size="sm" onClick={onClose}>
          Close
        </Button>
      </div>
    </Drawer>
  );
}

/** Render an audit value. ``null`` means "env default" (either the
 *  field had no override before the change, or this row is a
 *  clear-to-default). */
function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "(default)";
  if (typeof v === "string") return JSON.stringify(v);
  return String(v);
}

/** Operator IDs are UUIDs. Trim to the first segment so the drawer
 *  stays readable; the full ID is still in the DOM as a title attribute
 *  if needed. Future work: resolve the user object so we can show the
 *  username instead. */
function formatOperator(id: string): string {
  if (id.length <= 8) return id;
  return id.slice(0, 8);
}
