---
id: dashboard/overview
title: Dashboard
category: dashboard
tags: [dashboard, analytics, health]
summary: At-a-glance health and activity for your library.
help_context: [dashboard.overview]
related: [rules/reference, automation/overview, integrations/overview]
---

# Dashboard

The dashboard is the home page. It tells you, in one screen:

- How many files are in your library, and how many of them have issues
  open (anything above severity `ok`).
- The severity distribution across your whole library and per library.
- Which rules are matching the most files right now.
- The current health of every integration.
- What scanned and what ran (jobs) recently.

## How the numbers are computed

The dashboard is a read-only view. Every panel is a SQL aggregation over
existing tables — there is no separate `dashboard_stats` table, and no
materialized view. That keeps the numbers honest (no staleness) at the
cost of a handful of small queries per page load. For a typical
self-hosted instance (low thousands of files, dozens of rules) this is
fast enough that we haven't found it worth caching.

The endpoints are:

| Path | What it returns |
|------|-----------------|
| `GET /api/v1/dashboard/overview` | Top-of-page metrics: counts and the severity histogram |
| `GET /api/v1/dashboard/libraries` | Per-library severity breakdown |
| `GET /api/v1/dashboard/integrations` | Health snapshot for each configured integration |
| `GET /api/v1/dashboard/top-rules?limit=N` | Rules ordered by current match count |
| `GET /api/v1/dashboard/recent-scans?limit=N` | Most recent scan runs |
| `GET /api/v1/dashboard/recent-job-runs?limit=N` | Most recent automation runs |
| `GET /api/v1/dashboard/sidebar-badges` | Counters for the sidebar badges |

All require an authenticated user; none require admin.

## Sidebar badges

The numbers next to **Files**, **Rules**, and **Optimization** in the
sidebar come from `/dashboard/sidebar-badges`:

- **Files** shows `issuesOpen` — files with `severity_rank > 10` (i.e.
  anything above plain `ok`).
- **Rules** shows `rulesEnabled` — the count of currently-enabled rules.
- **Optimization** shows `activeOptimizations` — items in the queue with
  status `queued` or `running`.

The badge query refetches every 60s in the background; explicit user
actions (creating a rule, queueing an optimization, etc.) invalidate the
query immediately so the badge reflects the change without waiting.

## When the dashboard is empty

A fresh install has no libraries, no integrations, and no rules.
Everything panel handles this case explicitly — the dashboard tells you
what to do next ("Add a library in Settings", "Connect Plex or Sonarr",
"Create a rule") instead of showing a screen of zeros.

## What's next

Time-windowed widgets — "issues opened this week", "match velocity per
rule", "scan throughput trends" — are not in Stage 8. They'd need new
indexed `evaluated_at`-style columns or a small denormalized rollup
table. Stage 13 polish will decide whether real deployments call for
either.
