/**
 * Account page (Stage 5 audit follow-up).
 *
 * Self-service surface for the signed-in user:
 *   1. Profile — edit display name + email (PATCH /auth/me).
 *   2. Password — change with current-password confirmation.
 *   3. Sessions — revoke every other active session.
 *
 * Resolves audit Issue 14: "Account page (self-edit + change
 * password)". Pre-Stage-5, the only profile-management surface was
 * an admin-only users panel; the operator had no way to update
 * their own email or change their password from the UI.
 */

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHead } from "@/components/ui/Card";
import { PageHeader } from "@/components/shell/PageHeader";
import {
  useChangePassword,
  useLogoutAll,
  useUpdateProfile,
} from "@/hooks/useAuth";
import { ApiError } from "@/services/apiClient";
import { toast } from "@/lib/toast";
import { useAuthStore } from "@/stores/authStore";

export function AccountPage() {
  const user = useAuthStore((s) => s.user);
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Account"
        sub="Update your profile, change your password, manage active sessions."
      />
      {user ? <ProfileCard /> : null}
      <PasswordCard />
      <SessionsCard />
    </div>
  );
}

// ── Profile ──────────────────────────────────────────────────
function ProfileCard() {
  const user = useAuthStore((s) => s.user);
  const update = useUpdateProfile();
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await update.mutateAsync({
        full_name: fullName.trim() || undefined,
        email: email.trim() || undefined,
      });
      toast("Profile updated", "ok");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Update failed: ${msg}`, "error", 5000);
    }
  }

  return (
    <Card>
      <CardHead title="Profile" subtitle="Your display name and email." />
      <CardBody>
        <form className="flex flex-col gap-3 max-w-md" onSubmit={onSubmit}>
          <label className="flex flex-col gap-1 text-[12px]">
            Display name
            <input
              className="settings-input"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder={user?.username ?? ""}
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px]">
            Email
            <input
              className="settings-input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <div>
            <Button
              type="submit"
              variant="accent"
              disabled={update.isPending}
            >
              {update.isPending ? "Saving…" : "Save profile"}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

// ── Password ─────────────────────────────────────────────────
function PasswordCard() {
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (next.length < 12) {
      toast("New password must be at least 12 characters.", "warn");
      return;
    }
    if (next !== confirm) {
      toast("New password and confirmation do not match.", "warn");
      return;
    }
    try {
      await change.mutateAsync({
        current_password: current,
        new_password: next,
      });
      toast("Password changed", "ok");
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Password change failed: ${msg}`, "error", 5000);
    }
  }

  return (
    <Card>
      <CardHead
        title="Password"
        subtitle="Change your account password. You'll stay signed in on this device."
      />
      <CardBody>
        <form className="flex flex-col gap-3 max-w-md" onSubmit={onSubmit}>
          <label className="flex flex-col gap-1 text-[12px]">
            Current password
            <input
              className="settings-input"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px]">
            New password
            <input
              className="settings-input"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
              minLength={12}
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px]">
            Confirm new password
            <input
              className="settings-input"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </label>
          <div>
            <Button
              type="submit"
              variant="accent"
              disabled={change.isPending}
            >
              {change.isPending ? "Changing…" : "Change password"}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

// ── Sessions ─────────────────────────────────────────────────
function SessionsCard() {
  const logoutAll = useLogoutAll();

  async function onRevoke() {
    if (
      !window.confirm(
        "Sign out of all OTHER sessions? Your current session here stays signed in.",
      )
    ) {
      return;
    }
    try {
      await logoutAll.mutateAsync();
      toast("Other sessions revoked", "ok");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Failed to revoke sessions: ${msg}`, "error", 5000);
    }
  }

  return (
    <Card>
      <CardHead
        title="Active sessions"
        subtitle="Sign out everywhere except this device."
      />
      <CardBody>
        <p className="text-[12.5px] text-muted-2 mb-3 max-w-md">
          Useful if you used a shared computer or suspect your
          credentials were exposed. Refresh tokens on every other
          session are revoked immediately.
        </p>
        <Button
          variant="ghost"
          onClick={onRevoke}
          disabled={logoutAll.isPending}
        >
          {logoutAll.isPending ? "Revoking…" : "Sign out other sessions"}
        </Button>
      </CardBody>
    </Card>
  );
}
