/**
 * Stage 1 — Drawer primitive.
 *
 * Right-anchored slide-in panel built on ``@radix-ui/react-dialog``.
 * Honours the design package ``.drawer`` + ``.drawer-bg`` contract:
 *   - Overlay: --bg @ 50% over rgba(0,0,0,0.4), 2px backdrop blur.
 *   - Panel: --surface, --border-left, --shadow-lg, full-height.
 *   - Width: ``min(580px, 100vw)`` via --drawer-w.
 *   - Slide-in 0.25s cubic-bezier(.2,.7,.2,1) from right.
 *
 * Composition:
 *
 *   <Drawer open={open} onOpenChange={setOpen} ariaLabel="File detail">
 *     <DrawerHead title={file.name} subtitle={file.path} onClose={...} />
 *     <DrawerBody>...</DrawerBody>
 *     <DrawerFoot>{actions}</DrawerFoot>
 *   </Drawer>
 *
 * Per Addendum item #18, this is the one canonical drawer model. The
 * existing ``HelpDrawer`` in ``components/shell/`` is the contextual help
 * surface and is allowed to keep its own implementation because it owns
 * keyboard shortcut and search ergonomics.
 */

import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

export interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** ARIA dialog title — required for screen readers. If a visible title is
   *  rendered via ``<DrawerHead title=...>``, pass the same string here. */
  ariaLabel: string;
  /** Optional ARIA description — same semantics as Modal's
   *  ``ariaDescription``. See ``Modal.tsx`` for the full rationale.
   *  Omitting the prop opts out of Radix's runtime "Missing
   *  Description" warning via ``aria-describedby={undefined}``. */
  ariaDescription?: string;
  /** Width override. Defaults to ``var(--drawer-w)`` which is min(580px, 100vw). */
  widthClassName?: string;
  children: ReactNode;
}

export function Drawer({
  open,
  onOpenChange,
  ariaLabel,
  ariaDescription,
  widthClassName = "w-drawer",
  children,
}: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={cn(
            "ui-overlay fixed inset-0 z-[70]",
            "bg-[color-mix(in_oklab,var(--bg)_50%,rgba(0,0,0,0.4))]",
            "backdrop-blur-[2px]",
          )}
        />
        <Dialog.Content
          aria-label={ariaLabel}
          // Opt out of Radix's "Missing Description" warning when
          // no description is supplied; let Radix auto-wire when
          // one is. See Modal.tsx for the full design note.
          {...(ariaDescription ? {} : { "aria-describedby": undefined })}
          className={cn(
            "ui-drawer fixed inset-y-0 right-0 z-[71]",
            "flex flex-col bg-surface border-l border-border shadow-lg",
            "max-w-[100vw]",
            widthClassName,
          )}
        >
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

export interface DrawerHeadProps {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose?: () => void;
  /** Extra actions rendered to the left of the close button. */
  actions?: ReactNode;
}

export function DrawerHead({ title, subtitle, onClose, actions }: DrawerHeadProps) {
  return (
    <div className="flex items-start gap-3 px-4 py-3.5 border-b border-border">
      <div className="min-w-0 flex-1">
        <h2 className="m-0 text-[15px] font-semibold tracking-tight truncate">{title}</h2>
        {subtitle ? (
          <div className="mt-0.5 text-[12px] text-muted truncate">{subtitle}</div>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-1.5 shrink-0">{actions}</div> : null}
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

export function DrawerBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex-1 overflow-y-auto px-4 py-4", className)}>{children}</div>;
}

export function DrawerFoot({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "px-4 py-3 border-t border-border bg-surface-2",
        "flex items-center justify-end gap-2",
        className,
      )}
    >
      {children}
    </div>
  );
}
