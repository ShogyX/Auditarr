import { create } from "zustand";

interface HelpContextState {
  /** Currently-active help context key (set by feature pages on mount). */
  activeKey: string | null;
  /** Whether the help drawer is open. */
  isOpen: boolean;
  setKey: (key: string | null) => void;
  open: (key?: string) => void;
  close: () => void;
  toggle: (key?: string) => void;
}

export const useHelpStore = create<HelpContextState>((set, get) => ({
  activeKey: null,
  isOpen: false,
  setKey: (key) => set({ activeKey: key }),
  open: (key) => set({ activeKey: key ?? get().activeKey, isOpen: true }),
  close: () => set({ isOpen: false }),
  toggle: (key) =>
    set((state) => ({
      isOpen: !state.isOpen,
      activeKey: key ?? state.activeKey,
    })),
}));
