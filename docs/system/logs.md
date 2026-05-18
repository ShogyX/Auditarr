---
id: system/logs
title: System logs
category: system
tags: [system, logs, debugging]
summary: In-app log viewer for diagnosing issues without shell access.
help_context: [system.logs]
related: [system/factory-reset]
---

# System logs

The Logs page surfaces Auditarr's structured logs in-app so operators don't need shell access to diagnose issues. Available under `System → Logs` for admins.

## What's visible

Auditarr emits structured JSON logs across categories: `api`, `worker`, `scanner`, `playback`, `rules`, `integrations`, `database`, `system`. The Logs page reads a rolling in-memory buffer (the last 10,000 records, configurable via `AUDITARR_LOG_BUFFER_SIZE`).

Each log row shows:

* Timestamp (UTC, displayed in the operator's local TZ)
* Severity (`debug` / `info` / `warning` / `error` / `critical`)
* Category
* Event name (the structured key — e.g. `scanner.library_scan_started`)
* Free-form message
* Expandable JSON payload with the full event data

## Filters

The toolbar offers:

* **Severity** — minimum level. Default `info`; switch to `warning` for an "anything bad" view.
* **Category** — filter to one source (e.g. just `playback` while debugging an integration issue).
* **Search** — substring match against the event name + message.
* **Since** — relative time window. Default `last 1 hour`.

The filtered count + the most recent error timestamp (if any) appear in the toolbar so operators can confirm the filter is doing what they expect.

## Export

The **Download** button exports the currently-filtered log set as newline-delimited JSON (`.ndjson`). The download path uses an authenticated `fetch` with a blob anchor — the operator's session token is included in the request header (a `window.location.href` redirect would drop the header and 401).

## Live tail

When the operator scrolls to the bottom of the list, the page enters "live tail" mode: new records append as they arrive (via the same SSE channel that drives the rest of the live-update UI). Scrolling up exits live tail; clicking **Resume tail** re-enters it.

## Cardinality and retention

The in-memory buffer is rolling — once full, the oldest records are evicted. Auditarr does not write logs to disk by default; operators wanting durable logs should run the backend with `--log-format json` and capture stdout via systemd journal, Docker logs, or a sidecar collector.

## What's NOT in the in-app log

* Request bodies — these can contain sensitive data and are dropped before the buffer.
* Secret values — `Authorization`, `Bearer`, `sk-*`, `api_key` patterns are scrubbed from log payloads before storage.
* Database query SQL — `AUDITARR_DATABASE_ECHO=true` puts SQL into stdout for development but never into the in-app buffer.
