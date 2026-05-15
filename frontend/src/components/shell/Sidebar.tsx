import { NavLink } from "react-router-dom";

import { Icon } from "@/components/ui/Icon";
import { useLogout } from "@/hooks/useAuth";
import { useSystemVersion } from "@/hooks/useSystem";
import { useUpdaterStatus } from "@/hooks/useUpdater";
import { cn } from "@/lib/cn";
import { usePluginNavEntries } from "@/plugins/registry";
import { useAuthStore } from "@/stores/authStore";
import { useUiStore } from "@/stores/uiStore";

import { BrandMark } from "./BrandMark";
import { CORE_NAV } from "./nav";

interface SidebarProps {
  /** Optional badge stat lookup, supplied once stats endpoint exists (Stage 8). */
  stats?: Partial<Record<"issuesOpen" | "rulesEnabled" | "activeOptimizations", number>>;
}

export function Sidebar({ stats }: SidebarProps) {
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const pluginNav = usePluginNavEntries();
  const user = useAuthStore((s) => s.user);
  const logout = useLogout();
  // Stage 11: surface the "update available" indicator next to the
  // Help & updates nav entry. We keep the data fetch in Sidebar (not in
  // CoreNav) because nav.ts is a pure config module and this only fires
  // a single shared query that the help page reuses.
  const updaterStatus = useUpdaterStatus();
  const updateAvailable = updaterStatus.data?.has_update ?? false;

  // Stage 5 audit fix (Issue 11): show the live image-stamped
  // version rather than a hardcoded "v1.0". ``app_version`` is the
  // release version that matches the changelog; ``sdk_version`` is
  // the in-source schema version (only bumps on breaking releases).
  // If the probe hasn't returned yet or fails, we fall back to the
  // original hardcoded string so the chip never disappears mid-paint.
  const systemVersion = useSystemVersion();
  const liveVersion =
    systemVersion.data?.app_version ?? systemVersion.data?.sdk_version;
  const versionLabel = liveVersion
    ? liveVersion.startsWith("v")
      ? liveVersion
      : `v${liveVersion}`
    : "v1.0";

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex flex-col w-sidebar",
        "bg-surface border-r border-border",
      )}
    >
      <div className="flex items-center gap-2 px-4 h-header text-text">
        <BrandMark size={28} />
        <span className="text-[15px] font-semibold tracking-tight">Auditarr</span>
        <span className="ml-auto font-mono text-[10.5px] text-muted">
          {versionLabel}
        </span>
      </div>

      <div className="px-4 pt-3 pb-1 text-[10.5px] uppercase tracking-[0.08em] text-muted-2 font-semibold">
        Workspace
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-3 flex flex-col gap-0.5">
        {CORE_NAV.map((item) => (
          <NavItemLink
            key={item.key}
            to={item.path}
            icon={item.icon}
            label={item.label}
            badge={item.badgeKey ? stats?.[item.badgeKey] : undefined}
            dot={item.key === "help" && updateAvailable}
            end={item.path === "/"}
          />
        ))}

        {pluginNav.length > 0 && (
          <>
            <div className="px-3 pt-3 pb-1 text-[10.5px] uppercase tracking-[0.08em] text-muted-2 font-semibold">
              Plugins
            </div>
            {pluginNav.map((entry) => (
              <NavItemLink
                key={entry.key}
                to={`/plugins/${entry.key}`}
                icon={entry.icon as never}
                label={entry.label}
                badge={entry.badge?.()}
              />
            ))}
          </>
        )}
      </nav>

      <div className="flex items-center gap-3 px-3 py-3 border-t border-border">
        <div className="h-8 w-8 rounded-full bg-surface-sunk text-text-2 text-[11px] font-semibold flex items-center justify-center border border-border">
          {initialsOf(user?.full_name ?? user?.username ?? "")}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-medium truncate">
            {user?.full_name || user?.username || "—"}
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted">
            <span className="dot ok" />
            <span className="capitalize">{user?.role ?? "user"}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className={cn(
            "h-7 w-7 rounded-[5px] inline-flex items-center justify-center",
            "border border-border bg-surface-2 text-text-2 hover:bg-[var(--hover)] transition-colors",
          )}
        >
          <Icon name={theme === "dark" ? "sun" : "moon"} size={14} />
        </button>
        <button
          type="button"
          onClick={() => logout.mutate()}
          aria-label="Sign out"
          title="Sign out"
          disabled={logout.isPending}
          className={cn(
            "h-7 w-7 rounded-[5px] inline-flex items-center justify-center",
            "border border-border bg-surface-2 text-text-2 hover:bg-[var(--hover)]",
            "disabled:opacity-50 transition-colors",
          )}
        >
          <Icon name="arrow_up_right" size={14} />
        </button>
      </div>
    </aside>
  );
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "·";
  if (parts.length === 1) return (parts[0]?.slice(0, 2) ?? "·").toUpperCase();
  return ((parts[0]?.[0] ?? "") + (parts[parts.length - 1]?.[0] ?? "")).toUpperCase();
}

function NavItemLink({
  to,
  icon,
  label,
  badge,
  dot,
  end,
}: {
  to: string;
  icon: Parameters<typeof Icon>[0]["name"];
  label: string;
  badge?: number;
  /** Show a small coloured dot in the right margin (e.g. update available). */
  dot?: boolean;
  end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 px-3 h-8 rounded-[6px] text-[13px]",
          "text-text-2 hover:bg-[var(--hover)] transition-colors",
          isActive && "bg-[var(--active)] text-text font-medium",
        )
      }
    >
      <Icon name={icon} size={16} />
      <span className="flex-1 truncate">{label}</span>
      {dot ? (
        <span
          className="h-2 w-2 rounded-full bg-sev-info"
          aria-label="Update available"
          title="Update available"
        />
      ) : null}
      {typeof badge === "number" ? (
        <span
          className={cn(
            "min-w-[20px] h-[18px] inline-flex items-center justify-center px-1.5",
            "rounded-full text-[10.5px] font-mono font-semibold",
            "bg-surface-sunk text-text-2 border border-border",
          )}
        >
          {badge.toLocaleString()}
        </span>
      ) : null}
    </NavLink>
  );
}
