/**
 * Stage 6 — ``Field`` form-row primitive.
 *
 * Promoted from feature-local copies that had accumulated in
 * ``features/rules/editorShared``, ``features/optimization``,
 * ``features/automation``, ``features/integrations``, and
 * ``features/notifications``. After Stage 6 there are five places
 * that need this primitive; the cost of keeping them in sync exceeds
 * the cost of a small shared component.
 *
 * Vocabulary (matches the design package):
 *   - 10.5px uppercase label, 0.06em letter-spacing, semibold
 *   - 1.5 line-height gap between label and control
 *   - control sits below label, full width
 *
 * Use with Stage 1 controls (``Input``, ``Select``, ``Textarea``,
 * ``Switch``):
 *
 *   <Field label="Name">
 *     <Input value={name} onChange={(e) => setName(e.target.value)} />
 *   </Field>
 *
 * For controls that need a description, pass the description as a
 * sibling child after the input. Field doesn't impose layout on its
 * children so descriptions, error helpers, and counters render
 * naturally in the same block.
 */

import type { ReactNode } from "react";

export interface FieldProps {
  label: string;
  children: ReactNode;
}

export function Field({ label, children }: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
        {label}
      </span>
      {children}
    </label>
  );
}
