/**
 * Stage 1 — Modal primitive.
 *
 * Built on ``@radix-ui/react-dialog``. Honours the design package contract:
 *   - Overlay: --bg @ 50% over rgba(0,0,0,0.4), 4px backdrop blur.
 *   - Surface: --surface, 1px --border, --radius-lg, --shadow-lg.
 *   - Top-anchored (60px top padding), horizontally centred.
 *   - Three sub-regions: head (border-bottom), body (scrolls), foot
 *     (--surface-2 background, justify-end actions).
 *
 * Sizes via token-backed Tailwind utilities:
 *   - sm  → 480px   (--modal-w-sm)
 *   - md  → 640px   (--modal-w-md)
 *   - lg  → 760px   (--modal-w-lg)
 *
 * Composition:
 *
 *   <Modal open={isOpen} onOpenChange={setOpen} ariaLabel="Delete rule">
 *     <ModalHead title="Delete rule?" onClose={() => setOpen(false)} />
 *     <ModalBody>Are you sure? This cannot be undone.</ModalBody>
 *     <ModalFoot>
 *       <Button onClick={() => setOpen(false)}>Cancel</Button>
 *       <Button variant="primary" onClick={confirm}>Delete</Button>
 *     </ModalFoot>
 *   </Modal>
 *
 * Radix handles focus trap, ``Escape`` to close, body-scroll lock, and
 * ``aria-*`` semantics. Per Addendum item #18 ("Modal / Drawer Governance"),
 * this is the one canonical modal interaction model — no alternative
 * variants, no nested-modal stacking.
 */

import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

export type ModalSize = "sm" | "md" | "lg";

const SIZE_CLASSES: Record<ModalSize, string> = {
  sm: "w-modal-sm",
  md: "w-modal-md",
  lg: "w-modal-lg",
};

export interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  size?: ModalSize;
  /** ARIA dialog title — required for screen readers. If a visible title is
   *  rendered via ``<ModalHead title=...>``, pass the same string here. */
  ariaLabel: string;
  /** Optional ARIA description — supplemental context for screen
   *  readers. Radix Dialog warns at runtime when neither a
   *  ``<Dialog.Description>`` nor an explicit ``aria-describedby``
   *  is provided. Passing a string here renders an sr-only
   *  ``Dialog.Description`` that Radix auto-wires; omitting the
   *  prop tells Radix that this dialog has no description (via
   *  ``aria-describedby={undefined}``), which is a valid opt-out
   *  per Radix's own docs. Either choice silences the warning. */
  ariaDescription?: string;
  children: ReactNode;
}

export function Modal({
  open,
  onOpenChange,
  size = "md",
  ariaLabel,
  ariaDescription,
  children,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={cn(
            "ui-overlay fixed inset-0 z-[80]",
            "bg-[color-mix(in_oklab,var(--bg)_50%,rgba(0,0,0,0.4))]",
            "backdrop-blur-[4px]",
          )}
        />
        <Dialog.Content
          // Radix Dialog handles focus trap + Escape + body-scroll-lock
          // independently of ``aria-modal``, and the WAI-ARIA spec says
          // the attribute is technically redundant when those are in
          // place. We still set it explicitly because (a) operator
          // accessibility audits look for it, and (b) our existing
          // ``BugHunt1.test.tsx`` pins the contract — Stage 5
          // primitive adoption must not regress that.
          aria-modal="true"
          // When no description is supplied, explicitly opt out so
          // Radix doesn't log the "Missing Description" runtime
          // warning. Passing ``aria-describedby={undefined}`` is
          // the documented Radix opt-out — Radix's runtime check
          // sees the prop as intentionally present-and-undefined
          // and skips the warning. When ``ariaDescription`` IS
          // supplied, we omit the prop here (spreading ``{}``) so
          // Radix can auto-wire to our ``<Dialog.Description>``.
          {...(ariaDescription ? {} : { "aria-describedby": undefined })}
          className={cn(
            "ui-modal fixed left-1/2 top-[60px] -translate-x-1/2 z-[81]",
            "max-h-[calc(100vh-120px)] overflow-hidden",
            "flex flex-col bg-surface border border-border rounded-lg shadow-lg",
            SIZE_CLASSES[size],
            "max-w-[calc(100vw-40px)]",
          )}
        >
          {/* Radix auto-wires aria-labelledby to this Title's id.
              We render it sr-only so the visible ``ModalHead`` h2 is
              free to choose its own typography. Consumers MUST render
              ``ModalHead`` (or another title) so the label is
              meaningful; ``ariaLabel`` is the prose used here AND a
              hidden fallback if no ModalHead is rendered. */}
          <Dialog.Title className="sr-only">{ariaLabel}</Dialog.Title>
          {ariaDescription ? (
            <Dialog.Description className="sr-only">
              {ariaDescription}
            </Dialog.Description>
          ) : null}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export interface ModalHeadProps {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose?: () => void;
}

export function ModalHead({ title, subtitle, onClose }: ModalHeadProps) {
  return (
    <div className="flex items-start justify-between gap-3 px-[22px] pt-[18px] pb-3 border-b border-border">
      <div className="min-w-0">
        <h2 className="m-0 text-[17px] font-semibold tracking-tight">{title}</h2>
        {subtitle ? (
          <div className="mt-0.5 text-[12.5px] text-muted">{subtitle}</div>
        ) : null}
      </div>
      {onClose ? (
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className={cn(
            "h-7 w-7 rounded-[5px] inline-flex items-center justify-center shrink-0",
            "border border-border bg-surface-2 text-text-2 hover:bg-[var(--hover)] transition-colors",
          )}
        >
          <Icon name="x" size={14} />
        </button>
      ) : null}
    </div>
  );
}

export function ModalBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("px-[22px] py-[18px] overflow-y-auto flex-1", className)}>{children}</div>
  );
}

export function ModalFoot({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "px-[18px] py-3 border-t border-border bg-surface-2",
        "flex items-center justify-end gap-2",
        className,
      )}
    >
      {children}
    </div>
  );
}
