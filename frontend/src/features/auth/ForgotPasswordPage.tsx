import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { BrandMark } from "@/components/shell/BrandMark";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { useRequestPasswordReset } from "@/hooks/useAuth";

export function ForgotPasswordPage() {
  const request = useRequestPasswordReset();
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await request.mutateAsync(email);
    } catch {
      // Server returns 202 unconditionally; surface a friendly state below.
    }
    setSubmitted(true);
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-bg p-6">
      <div className="flex items-center gap-2 mb-6 text-text">
        <BrandMark size={28} />
        <span className="text-[15px] font-semibold tracking-tight">Auditarr</span>
      </div>
      <Card className="w-full max-w-sm">
        <CardBody className="flex flex-col gap-4">
          <h1 className="text-[16px] font-semibold tracking-tight m-0">Reset your password</h1>

          {submitted ? (
            <p className="text-[13px] text-text-2 leading-relaxed m-0">
              If <span className="font-mono">{email}</span> matches an account, you will receive a
              reset link within a minute. Check your inbox and follow the link to set a new
              password.
            </p>
          ) : (
            <form onSubmit={onSubmit} className="flex flex-col gap-3">
              <p className="text-[12.5px] text-muted m-0">
                Enter the email address associated with your account.
              </p>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="you@example.com"
                className={
                  "h-9 px-3 text-[13px] bg-surface border border-border rounded-md " +
                  "focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
                }
              />
              <Button
                type="submit"
                variant="primary"
                size="md"
                disabled={request.isPending}
                className="w-full"
              >
                {request.isPending ? "Sending…" : "Send reset link"}
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
