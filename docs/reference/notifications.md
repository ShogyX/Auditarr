---
id: reference/notifications
title: Notification providers
category: reference
tags: [notifications, email, push]
summary: Configure SMTP, Pushbullet, and Pushover; severity filtering and scheduling.
help_context: [notifications.providers]
related: [rules/severity, rules/actions]
---

# Notification providers

Auditarr ships three providers in core: SMTP, Pushbullet, and Pushover.
Plugins can register additional providers.

## SMTP

Configure SMTP through the `AUDITARR_SMTP_*` environment variables. Set
`AUDITARR_SMTP_BACKEND=console` for development — the message is logged
instead of sent, useful for debugging templates.

Templates are Jinja2 files under `app/services/email/templates/`.

## Push providers

Pushbullet and Pushover use their official APIs. API keys are stored
encrypted at rest.

## Severity filtering

Each provider can be configured with a minimum severity rank. Anything
below that rank is silently dropped — useful for routing only `error` and
`crit` to push, while `warn` and above still email a daily digest.

## Rate limiting

Per-channel rate limits prevent a runaway rule from flooding channels.
Defaults: 30 messages per minute per channel; per-rule overrides can lower
this further.
