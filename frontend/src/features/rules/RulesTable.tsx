/**
 * Stage 4 — Rules table.
 *
 * Extracted from the inline ``CustomTab``, ``BuiltinTab``, and
 * ``RuleRow`` in ``RulesPage.tsx``. The two tabs render the same
 * table structure with different data + handlers; the unified
 * ``RulesTable`` component takes a ``variant`` prop to switch the
 * empty-state copy without duplicating the rendering loop.
 *
 * Preserves the exact DOM contract used by the existing tests:
 *
 *   - ``<table class="files-table" role="grid">`` — shared with Files
 *   - ``<tr class="files-table-row rules-row [is-disabled is-builtin]">``
 *   - ``.rules-table-toggle`` cell for the on/off switch
 *   - ``.rules-row-actions`` cell with stopPropagation
 *   - role="switch" + aria-checked on the toggle
 *
 * As with FilesTable, adopting the Stage 1 ``DataGrid`` primitive
 * would invalidate ~10 test selectors and is deferred to a future
 * "Stage 4b — DataGrid adoption" gated on a visual baseline.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type { Rule, useRules } from "@/hooks/useRules";
import { cn } from "@/lib/cn";

import { deriveSeverity, uniqueActionTypes } from "./rulesShared";

export type RulesTableVariant = "custom" | "builtin";

export interface RulesTableProps {
  variant: RulesTableVariant;
  /** Underlying query — provides loading / error / empty signal. */
  query: ReturnType<typeof useRules>;
  /** Rows to render. For Custom this is filtered by search; for
   *  Built-in it's the raw query.data. */
  visibleRules: Rule[];
  onEdit: (rule: Rule) => void;
  onToggle: (rule: Rule) => void;
  onDuplicate: (rule: Rule) => void;
  /** Custom tab only. Built-in's onDelete is a no-op because the
   *  row's button is disabled for built-ins. */
  onDelete?: (rule: Rule) => void;
}

export function RulesTable({
  variant,
  query,
  visibleRules,
  onEdit,
  onToggle,
  onDuplicate,
  onDelete,
}: RulesTableProps) {
  if (query.isLoading) {
    return (
      <div className="px-4 py-12">
        <LoadingState
          label={
            variant === "builtin"
              ? "Loading built-in rules…"
              : "Loading rules…"
          }
        />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="px-4 py-12">
        <ErrorState
          title={
            variant === "builtin"
              ? "Failed to load built-in rules"
              : "Failed to load rules"
          }
          description={(query.error as Error)?.message}
        />
      </div>
    );
  }
  // Empty state: the Custom tab distinguishes between "no rules at all"
  // (call to action: create one) and "no rules match the search"
  // (call to action: clear the search). Built-in has only one empty
  // case in practice — a seeding failure — so we surface it directly.
  if (variant === "custom") {
    if ((query.data?.length ?? 0) === 0) {
      return (
        <div className="px-4 py-12">
          <EmptyState
            icon="rules"
            title="No rules yet"
            description="Create a rule to start classifying files, or check the Suggestions tab for data-driven recommendations."
          />
        </div>
      );
    }
    if (visibleRules.length === 0) {
      return (
        <div className="px-4 py-12">
          <EmptyState
            icon="rules"
            title="No rules match"
            description="Clear the search to see every rule, or try a different term."
          />
        </div>
      );
    }
  } else if ((query.data?.length ?? 0) === 0) {
    // This empty-state shouldn't be reached in practice — the
    // server seeds builtins at startup — but if seeding failed
    // the tab should explain rather than render a confusing blank.
    return (
      <div className="px-4 py-12">
        <EmptyState
          icon="rules"
          title="No built-in rules"
          description="Built-in rules are seeded at startup; if you're seeing this, the server may have failed to seed them. Check server logs."
        />
      </div>
    );
  }

  return (
    <div className="files-table-wrap">
      <table className="files-table" role="grid">
        <thead>
          <tr>
            <th className="rules-table-toggle">State</th>
            <th>Name</th>
            <th>Severity</th>
            <th>Actions</th>
            <th className="num">Priority</th>
            <th className="num">Matches</th>
            <th>Last eval</th>
            <th aria-label="Row actions" />
          </tr>
        </thead>
        <tbody>
          {visibleRules.map((rule) => (
            <RuleRow
              key={rule.id}
              rule={rule}
              onEdit={() => onEdit(rule)}
              onToggle={() => onToggle(rule)}
              onDuplicate={() => onDuplicate(rule)}
              onDelete={onDelete ? () => onDelete(rule) : () => undefined}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RuleRow({
  rule,
  onEdit,
  onToggle,
  onDuplicate,
  onDelete,
}: {
  rule: Rule;
  onEdit: () => void;
  onToggle: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const severity = deriveSeverity(rule);
  const actionTypes = uniqueActionTypes(rule);
  const isBuiltin = !!rule.is_builtin;
  return (
    <tr
      className={cn(
        "files-table-row rules-row",
        !rule.enabled && "is-disabled",
        isBuiltin && "is-builtin",
      )}
      // Stage 30: built-in rows now navigate to the routed
      // editor in read-only mode (was: no-op in Stage 29). The
      // editor renders a banner + disabled inputs to make the
      // read-only state visible, and exposes Duplicate as the
      // primary CTA there.
      onClick={onEdit}
    >
      <td className="rules-table-toggle" onClick={(e) => e.stopPropagation()}>
        {/* Stage 8 audit fix (Issue 10): the switch is already
            role="switch" with aria-checked, which is the correct
            accessibility contract — but the tooltip title was just
            the action word ("Enable" / "Disable") which on hover
            reads ambiguously: an enabled rule shows "Disable" which
            a hurried operator could misread as "currently disabled".
            We now spell out state AND action in both title and
            aria-label so the meaning is unambiguous on every read. */}
        <button
          type="button"
          role="switch"
          aria-checked={rule.enabled}
          onClick={onToggle}
          className={cn("settings-switch", rule.enabled && "is-on")}
          title={
            rule.enabled
              ? "Currently enabled — click to disable"
              : "Currently disabled — click to enable"
          }
          aria-label={
            rule.enabled
              ? `Disable ${rule.name} (currently enabled)`
              : `Enable ${rule.name} (currently disabled)`
          }
        >
          <span className="settings-switch-thumb" />
        </button>
      </td>
      <td>
        <div className="min-w-0">
          <div className="text-[13px] font-medium truncate flex items-center gap-1.5">
            {rule.name}
            {isBuiltin ? (
              <Pill sev="info" title="Seeded by Auditarr; read-only">
                Built-in
              </Pill>
            ) : null}
            {!rule.enabled ? (
              <span className="text-[10.5px] uppercase tracking-wide text-muted-2 font-semibold">
                disabled
              </span>
            ) : null}
          </div>
          {rule.description ? (
            <div className="text-[11.5px] text-muted-2 truncate">
              {rule.description}
            </div>
          ) : null}
        </div>
      </td>
      <td>
        {severity ? (
          <Pill sev={severity}>{severity}</Pill>
        ) : (
          <span className="text-muted-2">—</span>
        )}
      </td>
      <td>
        <div className="flex gap-1 flex-wrap">
          {actionTypes.length > 0 ? (
            actionTypes.map((a) => <Tag key={a}>{a}</Tag>)
          ) : (
            <span className="text-muted-2">—</span>
          )}
        </div>
      </td>
      <td className="num font-mono">{rule.priority}</td>
      <td className="num font-mono">
        {rule.last_match_count > 0 ? (
          rule.last_match_count.toLocaleString()
        ) : (
          <span className="text-muted-2">0</span>
        )}
      </td>
      <td className="text-[11.5px] text-muted">
        {rule.last_evaluated_at ? (
          new Date(rule.last_evaluated_at).toLocaleDateString()
        ) : (
          <span className="text-muted-2">never</span>
        )}
      </td>
      <td className="rules-row-actions" onClick={(e) => e.stopPropagation()}>
        <Button
          size="sm"
          variant={isBuiltin ? "primary" : "ghost"}
          onClick={onDuplicate}
          // Stage 29: when the row is a builtin, Duplicate is the
          // primary CTA — it's the path to a writable copy.
          title={
            isBuiltin
              ? "Duplicate as a custom rule (the copy is writable)"
              : "Duplicate this rule"
          }
          aria-label={
            isBuiltin
              ? `Duplicate ${rule.name} as a custom rule`
              : `Duplicate ${rule.name}`
          }
        >
          <Icon name="duplicate" size={12} />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onDelete}
          disabled={isBuiltin}
          title={
            isBuiltin
              ? "Built-in rules can't be deleted. Disable instead."
              : "Delete this rule"
          }
          aria-label={`Delete ${rule.name}`}
        >
          <Icon name="trash" size={12} />
        </Button>
      </td>
    </tr>
  );
}
