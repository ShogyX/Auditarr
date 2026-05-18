import type { IconName } from "@/components/ui/Icon";

export interface CoreNavItem {
  key: string;
  label: string;
  icon: IconName;
  path: string;
  /** Stat key to read from a future stats endpoint. */
  badgeKey?: "issuesOpen" | "rulesEnabled" | "activeOptimizations";
}

export const CORE_NAV: readonly CoreNavItem[] = [
  { key: "dashboard", label: "Dashboard", icon: "dashboard", path: "/" },
  { key: "files", label: "Files", icon: "files", path: "/files", badgeKey: "issuesOpen" },
  { key: "rules", label: "Rules", icon: "rules", path: "/rules", badgeKey: "rulesEnabled" },
  // Stage 10 audit fix (Issue 15): Automation merged into the
  // Rules page as a tab. Its previous nav entry is gone; the
  // ``/automation`` route still works (AppRoutes redirects it to
  // ``/rules?tab=automation``) so bookmarks and prior deep-links
  // continue to land in the right place.
  {
    key: "optimization",
    label: "Optimization",
    icon: "optimize",
    path: "/optimization",
    badgeKey: "activeOptimizations",
  },
  { key: "integrations", label: "Integrations", icon: "integrations", path: "/integrations" },
  { key: "notifications", label: "Notifications", icon: "notifications", path: "/notifications" },
  { key: "plugins", label: "Plugins", icon: "folder", path: "/plugins" },
  // v1.9 audit fix (OP-12): expose the Logs page so admins can
  // find it. Stage 8.1 added the page but no nav entry surfaced
  // it. Admin-only at the API layer; non-admins still see the
  // entry but the page itself renders a 403 message.
  { key: "logs", label: "Logs", icon: "server", path: "/system/logs" },
  { key: "settings", label: "Settings", icon: "settings", path: "/settings" },
  // Stage 12 audit fix (Issue 17): label was "Help & updates"
  // — updates have moved to the new Changelog entry below, so
  // Help is now docs-only.
  { key: "help", label: "Help", icon: "help", path: "/help" },
  // Stage 12 audit fix (Issue 17): Changelog is now its own nav
  // entry. The Help page hosts documentation only; the Changelog
  // page hosts version-history content + the updater panel. The
  // ``clock`` icon reuses what previously labeled Automation
  // (removed in Stage 10) — a clock reads naturally as "what
  // happened over time".
  { key: "changelog", label: "Changelog", icon: "clock", path: "/changelog" },
];
