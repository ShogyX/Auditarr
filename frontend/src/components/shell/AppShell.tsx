import { useEffect } from "react";
import { Outlet } from "react-router-dom";

import { ScanProgressBar } from "@/components/ui/ScanProgressBar";
import { useSidebarBadges } from "@/hooks/useDashboard";
import { useScanProgressWs } from "@/hooks/useScanProgress";
import { applyAccent, applyTheme } from "@/lib/accent";
import { useHelpStore } from "@/stores/helpStore";
import { useUiStore } from "@/stores/uiStore";

import { HelpDrawer } from "./HelpDrawer";
import { Sidebar } from "./Sidebar";
import { TopNav } from "./TopNav";

export function AppShell() {
  // Stage 13 (plan §605, §616) — subscribe to the scan WS
  // bus ONCE at shell level so progress survives navigation.
  // Pre-Stage-13 the subscription lived inside ``useScanProgress``
  // which was mounted per-component — navigating away unmounted
  // the badge and reset state. With the subscription up here
  // and state in the central ``scanProgressStore``, the bar
  // stays consistent across all routes.
  useScanProgressWs();

  const nav = useUiStore((s) => s.nav);
  // Stage 20: subscribe to theme + accent so the DOM is always in sync
  // with the store. Without this effect, the persist middleware can
  // rehydrate AFTER bootstrapUi() ran with stale defaults — the store
  // value flips on click but the DOM attribute never gets re-applied
  // because applyTheme only runs inside the action, not on rehydrate.
  const theme = useUiStore((s) => s.theme);
  const accent = useUiStore((s) => s.accent);
  useEffect(() => {
    applyTheme(theme);
    applyAccent(accent, theme);
  }, [theme, accent]);

  const toggleHelp = useHelpStore((s) => s.toggle);
  const badges = useSidebarBadges();

  // Cmd/Ctrl + / opens the contextual help drawer from anywhere in the app.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault();
        toggleHelp();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleHelp]);

  if (nav === "top") {
    return (
      <div className="min-h-screen bg-bg text-text">
        <TopNav stats={badges.data} />
        {/* Stage 2: .app-main-top encodes the header offset + overflow-x
            in static CSS so the layout is stable on first paint and
            wide content can't push the page into horizontal scroll. */}
        <main className="app-main-top pt-header min-h-screen">
          {/* v1.9 Stage 1.1 — global scan progress bar. Always
              mounted; ``ScanProgressBar`` self-hides when no scan
              is running, so this only takes vertical space when
              an operator actually needs the feedback. Pre-1.9 the
              bar lived only inside FilesPage / DashboardPage, so
              operators on Rules / Integrations / Settings had no
              way to see a long scan was still going. */}
          <ScanProgressBar className="px-4 pt-2" />
          <Outlet />
        </main>
        <HelpDrawer />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <Sidebar stats={badges.data} />
      {/* Stage 2: .app-main encodes the sidebar offset + overflow-x in
          static CSS so the layout is stable on first paint (no
          "jumps-left-then-corrects" flash) and wide content can't
          push the page into horizontal scroll. Tailwind utilities
          are kept for visual back-compat — values are identical. */}
      <main className="app-main pl-sidebar min-h-screen flex flex-col">
        {/* v1.9 Stage 1.1 — global scan progress bar (see comment
            in the top-nav branch above for rationale). */}
        <ScanProgressBar className="px-4 pt-2" />
        <Outlet />
      </main>
      <HelpDrawer />
    </div>
  );
}
