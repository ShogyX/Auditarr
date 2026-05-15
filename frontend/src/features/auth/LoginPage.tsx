import { useState, type FormEvent, type InputHTMLAttributes, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { BrandMark } from "@/components/shell/BrandMark";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { useLogin } from "@/hooks/useAuth";
import { ApiError } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const tokens = useAuthStore((s) => s.tokens);
  const login = useLogin();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (tokens?.accessToken) {
    const dest = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? "/";
    return <Navigate to={dest} replace />;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ login: identifier, password });
      navigate("/", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Sign-in failed. Check your credentials and try again.",
      );
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
          <div>
            <h1 className="text-[16px] font-semibold tracking-tight m-0">Sign in</h1>
            <p className="text-[12.5px] text-muted mt-1 m-0">Use your Auditarr credentials.</p>
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-3" autoComplete="on">
            <Field label="Username or email">
              <Input
                autoFocus
                autoComplete="username"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                required
              />
            </Field>
            <Field
              label="Password"
              hint={
                <Link to="/forgot" className="text-[11.5px] text-muted hover:text-text-2">
                  Forgot?
                </Link>
              }
            >
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={1}
              />
            </Field>

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
              disabled={login.isPending}
              className="w-full"
            >
              {login.isPending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center justify-between text-[11.5px] font-medium text-muted">
        {label}
        {hint}
      </span>
      {children}
    </label>
  );
}

function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={
        "h-9 px-3 text-[13px] bg-surface border border-border rounded-md " +
        "focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent " +
        "placeholder:text-muted-2"
      }
    />
  );
}
