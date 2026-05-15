---
id: notifications/overview
title: Notifications
category: notifications
tags: [notifications, channels, alerts, dispatch]
summary: Deliver rule alerts to email, webhook, Discord, Slack, or Apprise.
help_context: [notifications.overview]
related: [rules/reference, integrations/overview]
---

# Notifications

A **notification channel** is a configured destination for alerts.
Channels are fired by rules: any rule with a `notify` action goes
through the dispatcher and out to every enabled channel whose severity
threshold the alert clears.

## Built-in channel kinds

| Kind     | What it sends to                                |
|----------|-------------------------------------------------|
| `email`  | SMTP server (host/port + STARTTLS or SSL)        |
| `webhook`| Any URL — receives an Auditarr-shaped JSON POST  |
| `discord`| Discord channel via incoming webhook URL         |
| `slack`  | Slack channel via incoming webhook URL           |
| `apprise`| ~70 destinations via the Apprise library         |

Plugins can register additional kinds through the SDK
(`context.register_notification_channel(provider)`).

The Apprise channel requires the optional `apprise` Python package to
be installed inside the container — without it the channel returns a
helpful failure rather than crashing.

## Severity thresholds

Every channel has a `min_severity_rank`. Rules produce alerts at one of
six ranks:

| Severity | Rank |
|----------|------|
| `ok`     | 10   |
| `info`   | 20   |
| `warn`   | 40   |
| `high`   | 60   |
| `error`  | 80   |
| `crit`   | 100  |

A channel only fires when the rule's severity rank meets or exceeds the
threshold. The default threshold is **40 (warn)** — a sensible "don't
spam me with info" starting point. Alerts below threshold appear in the
delivery log with status `skipped` so you can see what *would* have
fired without the threshold.

## Templating

Each channel can optionally override the subject and body templates via
the `subject_template` and `body_template` keys in its `config`. Both
are rendered with Jinja2. Available variables:

- `severity`, `severity_rank`
- `rule_id`, `rule_name`
- `media_file_id`, `path`, `filename`, `library_name`
- `message` — the rule's `notify.message` if set
- `time` — ISO-8601 UTC timestamp

A broken template (typo, undefined variable) falls back to the default
rather than dropping the alert.

## The delivery log

Every send attempt — including `skipped` ones — is written to
`notification_deliveries`. The log preserves the channel name and kind
denormalized so deleting a channel doesn't erase its history. Rows
include `subject`, `body`, `context`, `duration_ms`, and (on failure)
the error detail.

The endpoint surface:

| Path | Purpose |
|------|---------|
| `GET /api/v1/notifications/kinds` | Channel kind directory |
| `GET /api/v1/notifications` | List configured channels |
| `POST /api/v1/notifications` | Create a channel (admin) |
| `GET /api/v1/notifications/deliveries` | Recent deliveries; filter by `channel_id` or `status` |
| `GET /api/v1/notifications/{id}` | Get one channel |
| `PATCH /api/v1/notifications/{id}` | Update (admin) |
| `DELETE /api/v1/notifications/{id}` | Delete (admin); log entries retain `channel_name` |
| `POST /api/v1/notifications/{id}/test` | Send a one-off test (admin) |

## How alerts get here from rules

A rule's `notify` action looks like this:

```json
{ "type": "notify", "channel": "ops", "message": "Big files" }
```

The `channel` and `message` fields are passed through to the dispatcher,
but channel routing is **not** done by name. Every enabled channel sees
every alert; thresholds determine who actually delivers. This keeps
rule definitions decoupled from channel renames — adding a Slack channel
doesn't require changing your rules.

The `channel` field on a `notify` action *is* available to templates
via `{{ message }}` and `{{ context.channel }}` so operators who want
strict routing can put rule identifiers in the message and filter
downstream.

## Test sending

The **Test** button on a channel sends a one-off info-severity alert
ignoring the threshold. Use it after configuring a new channel — if the
test arrives in your inbox, your config is correct.

Test sends also write a row to the delivery log marked with
`context.trigger = "manual_test"`.
