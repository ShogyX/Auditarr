---
id: account/profile
title: Account & profile
category: account
tags: [account, profile, password, sessions, security]
summary: Manage your username, email, password, and active sessions from /account.
help_context: [account.profile, settings.account]
related: [getting-started/installation]
---

# Account & profile

The Account page (`/account`) is where you manage your own login
credentials and review which sessions are currently authenticated.
It's distinct from Settings, which manages workspace-level
configuration — Account is **per-user**, Settings is **per-deployment**.

## Sections

### Profile

Edit your display name, username, and email address. Username
changes propagate to audit logs (existing entries keep the old
username for accuracy; new entries use the new one).

### Password

Change your password. Required: your current password (the form
verifies it before accepting the new one) and a new password meeting
the deployment's password policy (length, complexity).

After a successful password change:

- All your other active sessions are terminated server-side.
- Your current session stays valid (you don't need to re-login).
- A `security.password_changed` audit entry is recorded.

### Sessions

List of currently-active refresh tokens for your account. Each row
shows:

| Column | Meaning |
|---|---|
| Created | When the session was opened (login event). |
| Last seen | When the session last refreshed its access token. |
| Device | Best-effort User-Agent parse — "Chrome on macOS", "Firefox on Linux", etc. |
| IP | Client IP at last refresh (subject to proxy / VPN). |
| Actions | Revoke this session (logs that device out). |

Revoking a session invalidates its refresh token immediately. The
device's access token will work until it expires (usually 15
minutes), then they're forced to re-login.

You cannot revoke your **current** session from this UI — use Sign out
from the global header for that.

## Security

The Account page is gated behind the same auth as the rest of the
app — there's no separate password-confirmation step to view it.
Password changes always require your current password regardless.

If you've forgotten your password and don't have an active session,
the recovery path is via the admin CLI (`auditarr user reset-password
<username>`) — there is no email-based reset by design (Auditarr is
self-hosted; no transactional email infrastructure is assumed).

## See also

- [Installation](/help/getting-started/installation) — the admin CLI
  password-reset flow.
