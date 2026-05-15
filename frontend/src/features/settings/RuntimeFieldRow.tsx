/**
 * Stage 2 — Runtime settings per-field card.
 *
 * Extracted from the inline ``RuntimeFieldRow`` in
 * ``RuntimeSettingsPanel``. Stage 2 adds:
 *
 *   - "Restart required" badge next to the impact pill for fields
 *     where ``restart_required === true``. No current spec sets this
 *     to true; the badge is rendered conditionally so it remains
 *     dormant until a real entry needs it.
 *   - "History" button on each field, opens the per-key history
 *     drawer in the parent.
 *   - "Elevated" badge for fields with ``sensitivity === "elevated"``.
 *     Pairs with the elevated-confirmation step in ConfirmApplyDialog.
 *
 * The structural DOM the existing tests depend on
 * (``getByRole("combobox")``, ``getByRole("button", { name: /restore
 * default/i })``) is preserved — the new history button has its own
 * accessible label so it doesn't collide.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import type { RuntimeField } from "@/hooks/useRuntimeSettings";
import { cn } from "@/lib/cn";

import { RuntimeInput } from "./RuntimeInput";
import { sameValue, type EditValue } from "./runtimeSettingsShared";

export interface RuntimeFieldRowProps {
  field: RuntimeField;
  proposed: EditValue | undefined;
  isApplied: boolean;
  onChange: (v: EditValue) => void;
  onRevert: () => void;
  onRestoreDefault: () => void;
  /** Stage 2: open the history drawer for this field. */
  onOpenHistory: () => void;
}

export function RuntimeFieldRow({
  field,
  proposed,
  isApplied,
  onChange,
  onRevert,
  onRestoreDefault,
  onOpenHistory,
}: RuntimeFieldRowProps) {
  const current =
    proposed !== undefined ? proposed : (field.value as EditValue);
  const dirty = proposed !== undefined && !sameValue(proposed, field.value);
  const isDefault = sameValue(current, field.env_default);

  return (
    <div
      className={cn(
        "runtime-field",
        dirty && "is-dirty",
        isApplied && "is-applied",
      )}
    >
      <div className="runtime-field-head">
        <code className="runtime-field-key">{field.key}</code>
        <span className="runtime-field-label">{field.label}</span>
        <span className="flex-1" />
        <span
          className="pill"
          title={
            field.impact === "immediate"
              ? "Applied on save"
              : "Applied at next worker tick"
          }
        >
          {field.impact === "immediate" ? "immediate" : "next tick"}
        </span>
        {/* Stage 2 metadata indicators. Dormant on today's spec
            (no entry has these flags set) — present so the UI can
            absorb them as soon as the schema does. */}
        {field.restart_required ? (
          <Pill sev="warn" title="Takes effect after restart">
            restart required
          </Pill>
        ) : null}
        {field.sensitivity === "elevated" ? (
          <Pill sev="warn" title="Requires elevated-confirmation on save">
            elevated
          </Pill>
        ) : null}
        {field.is_override ? <Pill>overridden</Pill> : null}
      </div>

      <p className="runtime-field-desc">{field.description}</p>

      <div className="runtime-field-controls">
        <RuntimeInput field={field} value={current} onChange={onChange} />
        <span className="runtime-field-default">
          default{" "}
          <code className="font-mono text-text-2">
            {String(field.env_default)}
          </code>
        </span>
        <span className="flex-1" />
        {/* Stage 2: history affordance. Always available — even on
            a field that's never been overridden, opening the drawer
            and seeing "no changes yet" is a meaningful signal. */}
        <Button
          size="sm"
          variant="ghost"
          onClick={onOpenHistory}
          title="View recent changes for this setting"
          aria-label={`View history for ${field.label}`}
        >
          <Icon name="clock" size={12} /> history
        </Button>
        {dirty ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onRevert}
            title="Discard change"
          >
            <Icon name="x" size={12} /> revert
          </Button>
        ) : !isDefault ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onRestoreDefault}
            title="Restore env default"
          >
            restore default
          </Button>
        ) : null}
      </div>

      {field.requires_warning && dirty ? (
        <div className="runtime-warn">
          <Icon
            name="alert"
            size={14}
            className="text-sev-warn shrink-0 mt-0.5"
          />
          <span>{field.requires_warning}</span>
        </div>
      ) : null}
    </div>
  );
}
