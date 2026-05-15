import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { BrandMark } from "@/components/shell/BrandMark";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { useConfirmPasswordReset } from "@/hooks/useAuth";
import { ApiError } from "@/services/apiClient";

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const confirm = useConfirmPasswordReset();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 12) {
      setError("Password must be at least 12 characters.");
      return;
    }
    if (password !== confirmPw) {
      setError("Passwords do not match.");
      return;
    }
    try {
      await confirm.mutateAsync({ token, new_password: password });
      navigate("/login?reset=ok", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reset failed. The link may have expired.");
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-bg p-6">
      <div className="flex items-center gap-2 mb-6 text-text">
        <BrandMark size={28} />
        <span className="text-[15px] font-semibold tracking-tight">Auditarr</span>
      </div>
      <Card className="w-full max-w-sm">
        <CardBody className="flex flex-col gap-4">
          <h1 className="text-[16px] font-semibold tracking-tight m-0">Choose a new password</h1>

          {!token ? (
            <p className="text-[13px] text-sev-error m-0">
              This reset link is missing its token. Request a new one.
            </p>
          ) : (
            <form onSubmit={onSubmit} className="flex flex-col gap-3">
              <input
                type="password"
                placeholder="New password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className={
                  "h-9 px-3 text-[13px] bg-surface border border-border rounded-md " +
                  "focus:outline-none focus:ring-2 focus:ring-accent"
                }
              />
              <input
                type="password"
                placeholder="Confirm password"
                autoComplete="new-password"
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
                required
                className={
                  "h-9 px-3 text-[13px] bg-surface border border-border rounded-md " +
                  "focus:outline-none focus:ring-2 focus:ring-accent"
                }
              />
              {error ? (
                <div className="text-[12px] text-sev-error flex items-center gap-1.5">
                  <Icon name="x" size={12} />
                  {error}
                </div>
              ) : null}
              <Button
                type="submit"
                variant="primary"
                size="md"
                disabled={confirm.isPending}
                className="w-full"
              >
                {confirm.isPending ? "Updating…" : "Set new password"}
              </Button>
            </form>
          )}

          <Link to="/login" className="text-[12px] text-muted hover:text-text-2">
            ← Back to sign in
          </Link>
        </CardBody>
      </Card>
    </div>
  );
}
