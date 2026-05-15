import { useMemo, type ComponentType } from "react";
import { create } from "zustand";

/** Frontend plugin contracts.
 *
 * Plugins NEVER mutate routing, providers, or shell layout. They register
 * declarative entries; the shell decides where to render them.
 */

export interface PluginNavEntry {
  /** Globally-unique key (also used as URL slug under /plugins/). */
  key: string;
  /** Sidebar label. */
  label: string;
  /** Icon name (must exist in the Icon registry). */
  icon: string;
  /** Optional badge selector — pulls a number from any store. */
  badge?: () => number | undefined;
}

export interface PluginPage {
  key: string;
  /** URL path under /plugins/<key>/, defaults to "". */
  path?: string;
  component: ComponentType;
  /** Help context key for the docs system. */
  helpContext?: string;
}

export interface PluginWidget {
  key: string;
  /** Where to render. */
  slot: "dashboard" | "files-sidebar" | "settings";
  component: ComponentType;
  /** Sort weight — lower renders first. */
  weight?: number;
}

export interface PluginSettingsSection {
  key: string;
  label: string;
  component: ComponentType;
}

export interface PluginRegistration {
  id: string;
  navEntries?: PluginNavEntry[];
  pages?: PluginPage[];
  widgets?: PluginWidget[];
  settings?: PluginSettingsSection[];
}

interface PluginRegistryState {
  registrations: Map<string, PluginRegistration>;
  register: (registration: PluginRegistration) => void;
  unregister: (id: string) => void;
}

export const usePluginRegistry = create<PluginRegistryState>((set) => ({
  registrations: new Map(),
  register: (registration) =>
    set((state) => {
      const next = new Map(state.registrations);
      next.set(registration.id, registration);
      return { registrations: next };
    }),
  unregister: (id) =>
    set((state) => {
      const next = new Map(state.registrations);
      next.delete(id);
      return { registrations: next };
    }),
}));

// ── Derived hooks ────────────────────────────────────────────
//
// CRITICAL: these hooks must NOT be expressed as Zustand selector methods.
// A selector like ``(s) => s.navEntries()`` returns a fresh array on every
// call, fails reference equality, and triggers an infinite re-render loop
// (React error #185) the moment any plugin populates entries. Subscribe to
// the registrations Map (whose reference only changes on register/unregister)
// and ``useMemo`` the derived view.

export function usePluginNavEntries(): PluginNavEntry[] {
  const registrations = usePluginRegistry((s) => s.registrations);
  return useMemo(
    () => Array.from(registrations.values()).flatMap((r) => r.navEntries ?? []),
    [registrations],
  );
}

export function usePluginPages(): PluginPage[] {
  const registrations = usePluginRegistry((s) => s.registrations);
  return useMemo(
    () => Array.from(registrations.values()).flatMap((r) => r.pages ?? []),
    [registrations],
  );
}

export function usePluginWidgets(slot: PluginWidget["slot"]): PluginWidget[] {
  const registrations = usePluginRegistry((s) => s.registrations);
  return useMemo(
    () =>
      Array.from(registrations.values())
        .flatMap((r) => r.widgets ?? [])
        .filter((w) => w.slot === slot)
        .sort((a, b) => (a.weight ?? 0) - (b.weight ?? 0)),
    [registrations, slot],
  );
}

export function usePluginSettings(): PluginSettingsSection[] {
  const registrations = usePluginRegistry((s) => s.registrations);
  return useMemo(
    () => Array.from(registrations.values()).flatMap((r) => r.settings ?? []),
    [registrations],
  );
}
