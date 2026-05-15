/** Lightweight imperative toast — drops into the #toasts container. */

export type ToastKind = "ok" | "warn" | "error" | "info";

export function toast(message: string, kind: ToastKind = "ok", ttlMs = 3500): void {
  if (typeof document === "undefined") return;
  const stack = document.getElementById("toasts");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  stack.appendChild(el);
  window.setTimeout(() => el.remove(), ttlMs);
}
