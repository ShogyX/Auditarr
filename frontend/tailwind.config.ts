import type { Config } from "tailwindcss";

/**
 * Stage 1: Tailwind theme mirrors the CSS custom properties declared in
 * ``src/styles/tokens.css``. Feature code can use either form:
 *
 *   class="px-page max-w-page"      ← Tailwind utilities backed by --page-*
 *   style={{ paddingInline: 'var(--page-pad-x)' }}  ← raw token reference
 *
 * The token file is authoritative; Tailwind here is a convenience surface.
 */
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "IBM Plex Sans",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "IBM Plex Mono",
          "ui-monospace",
          "SF Mono",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        // Surface chrome (greyscale only — accents go on signal elements)
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        "surface-sunk": "var(--surface-sunk)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        text: "var(--text)",
        "text-2": "var(--text-2)",
        muted: "var(--muted)",
        "muted-2": "var(--muted-2)",

        // Single accent (user-switchable)
        accent: "var(--accent)",
        "accent-2": "var(--accent-2)",
        "accent-soft": "var(--accent-soft)",
        "accent-fg": "var(--accent-fg)",

        // Severity / signal palette — pills, dots, bars only
        "sev-ok": "var(--sev-ok)",
        "sev-info": "var(--sev-info)",
        "sev-warn": "var(--sev-warn)",
        "sev-high": "var(--sev-high)",
        "sev-error": "var(--sev-error)",
        "sev-crit": "var(--sev-crit)",

        // Categories
        "cat-media": "var(--cat-media)",
        "cat-subtitle": "var(--cat-subtitle)",
        "cat-image": "var(--cat-image)",
        "cat-metadata": "var(--cat-metadata)",
        "cat-junk": "var(--cat-junk)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        DEFAULT: "var(--shadow)",
        lg: "var(--shadow-lg)",
      },
      spacing: {
        sidebar: "var(--sidebar-w)",
        header: "var(--header-h)",
        // Stage 1: promoted layout tokens
        "page-x": "var(--page-pad-x)",
        "page-y-top": "var(--page-pad-y-top)",
        "page-y-bottom": "var(--page-pad-y-bottom)",
        "row-h": "var(--row-h)",
        "row-h-dense": "var(--row-h-dense)",
        "header-row-h": "var(--header-row-h)",
        "toolbar-h": "var(--toolbar-h)",
      },
      maxWidth: {
        page: "var(--page-max-width)",
      },
      width: {
        // Drawer / modal widths exposed as Tailwind utilities for primitives.
        drawer: "var(--drawer-w)",
        "modal-sm": "var(--modal-w-sm)",
        "modal-md": "var(--modal-w-md)",
        "modal-lg": "var(--modal-w-lg)",
      },
      height: {
        "row-h": "var(--row-h)",
        "row-h-dense": "var(--row-h-dense)",
        "header-row-h": "var(--header-row-h)",
        "toolbar-h": "var(--toolbar-h)",
      },
    },
  },
  plugins: [],
};

export default config;
