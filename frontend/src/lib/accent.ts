/** Single-accent switcher. Mirrors the original Auditarr accent options. */

export type AccentName = "indigo" | "graphite" | "emerald" | "amber";

interface AccentSpec {
  l: number;
  c: number;
  h: number;
  dl: number;
  ds: number;
}

export const ACCENTS: Record<AccentName, AccentSpec> = {
  indigo: { l: 0.52, c: 0.16, h: 268, dl: 0.68, ds: 0.95 },
  graphite: { l: 0.3, c: 0.02, h: 260, dl: 0.85, ds: 0.92 },
  emerald: { l: 0.55, c: 0.14, h: 158, dl: 0.72, ds: 0.95 },
  amber: { l: 0.65, c: 0.16, h: 62, dl: 0.8, ds: 0.95 },
};

/** Apply an accent to the document root for the current theme. */
export function applyAccent(name: AccentName, theme: "light" | "dark"): void {
  const a = ACCENTS[name] ?? ACCENTS.indigo;
  const root = document.documentElement;
  if (theme === "dark") {
    root.style.setProperty("--accent", `oklch(${a.dl} ${a.c} ${a.h})`);
    root.style.setProperty("--accent-2", `oklch(${Math.min(a.dl + 0.06, 0.92)} ${a.c} ${a.h})`);
    root.style.setProperty("--accent-soft", `oklch(0.30 ${Math.min(a.c, 0.12)} ${a.h} / 0.35)`);
  } else {
    root.style.setProperty("--accent", `oklch(${a.l} ${a.c} ${a.h})`);
    root.style.setProperty("--accent-2", `oklch(${Math.min(a.l + 0.1, 0.72)} ${a.c} ${a.h})`);
    root.style.setProperty("--accent-soft", `oklch(${a.ds} ${Math.min(a.c, 0.05)} ${a.h})`);
  }
}

/** Apply theme attribute to <html>. */
export function applyTheme(theme: "light" | "dark"): void {
  document.documentElement.setAttribute("data-theme", theme);
}
