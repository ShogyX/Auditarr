import { useEffect } from "react";

import { useHelpStore } from "@/stores/helpStore";

/**
 * Bind the current page's help context key. Pages call this near their
 * top-level so the help drawer always knows what to show:
 *
 * ```tsx
 * useHelpKey("rules.conditions");
 * ```
 *
 * The key is cleared on unmount so route transitions don't leave stale state.
 */
export function useHelpKey(key: string | null): void {
  const setKey = useHelpStore((s) => s.setKey);
  useEffect(() => {
    setKey(key);
    return () => setKey(null);
  }, [key, setKey]);
}
