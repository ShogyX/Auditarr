import { NavLink } from "react-router-dom";

import { Icon } from "@/components/ui/Icon";
import { useLogout } from "@/hooks/useAuth";
import { useUpdaterStatus } from "@/hooks/useUpdater";
import { cn } from "@/lib/cn";
import { useAuthStore } from "@/stores/authStore";
import { useUiStore } from "@/stores/uiStore";

import { BrandMark } from "./BrandMark";
import { CORE_NAV } from "./nav";

interface TopNavProps {
  /** Currently unused — TopNav is compact and doesn't render badges. */
  stats?: Partial<Record<"issuesOpen" | "rulesEnabled" | "activeOptimizations", number>>;
}

export function TopNav(_props: TopNavProps = {}) {
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const user = useAuthStore((s) => s.user);
  const logout = useLogout();
  // Stage 14: surface the update-available dot in TopNav layout too —
  // it was only wired to Sidebar in Stage 11.
  const updaterStatus = useUpdaterStatus();
  const updateAvailable = updaterStatus.data?.has_update ?? false;

  return (
    <div
      className={cn(
        "fixed top-0 inset-x-0 z-30 flex items-center gap-3 h-header px-4",
        "bg-surface border-b border-border",
      )}
    >
      <BrandMark size={24} />
      <span className="text-[14px] font-semibold tracking-tight">Auditarr</span>
      <nav className="flex items-center gap-0.5 ml-2">
        {CORE_NAV.map((item) => (
          <NavLink
            key={item.key}
            to={item.path}
            end={item.path === "/"}
            className={({ isActive }) =>
              cn(
                "relative h-8 inline-flex items-center px-3 rounded-[6px] text-[12.5px]",
                "text-text-2 hover:bg-[var(--hover)] transition-colors",
                isActive && "bg-[var(--active)] text-text font-medium",
              )
            }
          >
            {item.label}
            {item.key === "help" && updateAvailable ? (
              <span
                className="ml-1.5 h-1.5 w-1.5 rounded-full bg-sev-info"
                aria-label="Update available"
                title="Update available"
              />
            ) : null}
          </NavLink>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-2">
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
        <NavLink
          to="/account"
          className="h-8 w-8 rounded-full bg-surface-sunk text-text-2 text-[11px] font-semibold flex items-center justify-center border border-border hover:bg-[var(--hover)] transition-colors"
          title={`${user?.full_name || user?.username || ""} — Account`}
          aria-label="Account"
        >
          {initialsOf(user?.full_name ?? user?.username ?? "")}
        </NavLink>
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
    </div>
  );
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "·";
  if (parts.length === 1) return (parts[0]?.slice(0, 2) ?? "·").toUpperCase();
  return ((parts[0]?.[0] ?? "") + (parts[parts.length - 1]?.[0] ?? "")).toUpperCase();
}
