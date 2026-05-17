/**
 * Stage 12 (plan §584) — Forced-change-password screen.
 *
 * Reached when a user logs in with ``must_change_password=true``
 * (set by ``confirm_password_reset`` after a terminal-OTP
 * reset). The LoginPage routes here automatically and the
 * ``RequireAuth`` shell check also bounces flagged users
 * here from any other page, so the operator can't slip past
 * by deep-linking.
 *
 * On successful change the backend clears the flag; we
 * refresh the user record from /auth/me then navigate to the
 * dashboard.
 */

import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { BrandMark } from "@/components/shell/BrandMark";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { useChangePassword } from "@/hooks/useAuth";
import { apiClient, ApiError } from "@/services/apiClient";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

export function ForcedChangePasswordPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const change = useChangePassword();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmNext, setConfirmNext] = useState("");
  const [error, setError] = useState<string | null>(null);

  // If the flag isn't set (e.g. user navigated here directly),
  // send them to the dashboard.
  if (user && !user.must_change_password) {
    return <Navigate to="/" replace />;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next !== confirmNext) {
      setError("New passwords don't match.");
      return;
    }
    if (next.length < 12) {
      setError("Password must be at least 12 characters.");
      return;
    }
    try {
      await change.mutateAsync({
        current_password: current,
        new_password: next,
      });
      // Refetch the user — the backend cleared must_change_password.
      const updated = await apiClient.get<AuthUser>("/auth/me");
      setUser(updated);
      navigate("/", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't change password. Try again.",
      );
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-bg p-6">
      <div className="flex items-center gap-2 mb-6 text-text">
        <BrandMark size={28} />
        <span className="text-[15px] font-semibold tracking-tight">
          Auditarr
        </span>
      </div>
      <Card className="w-full max-w-sm">
        <CardBody className="flex flex-col gap-4">
          <div>
            <h1 className="text-[16px] font-semibold tracking-tight m-0">
              Set a new password
            </h1>
            <p className="text-[12.5px] text-muted mt-1 m-0">
              You reset your password from the server logs. Choose a
              permanent password before continuing.
            </p>
          </div>

          {error ? (
            <div
              role="alert"
              className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-[12px] text-red-700 dark:text-red-300"
            >
              {error}
            </div>
          ) : null}

          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-[12px] text-text">
              Current password (the one-time password)
              <input
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                required
                autoComplete="current-password"
                className="h-9 px-3 text-[13px] bg-surface border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
              />
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-text">
              New password
              <input
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                required
                minLength={12}
                autoComplete="new-password"
                className="h-9 px-3 text-[13px] bg-surface border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
              />
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-text">
              Confirm new password
              <input
                type="password"
                value={confirmNext}
                onChange={(e) => setConfirmNext(e.target.value)}
                required
                minLength={12}
                autoComplete="new-password"
                className="h-9 px-3 text-[13px] bg-surface border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
              />
            </label>
            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={change.isPending}
              className="w-full"
            >
              {change.isPending ? "Saving…" : "Set password"}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
