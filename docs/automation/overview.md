---
id: automation/overview
title: Automation overview
category: automation
tags: [automation, schedules, jobs, optimization-queue]
summary: Schedule background jobs, browse run history, and inspect the optimization queue.
help_context: [automation.overview]
related: [rules/reference, integrations/overview]
---

# Automation overview

The automation engine is what turns one-off operations (run a scan, run a
healthcheck, sync tags, evaluate rules) into things that happen on a
cadence without anyone clicking a button.

It has three pieces:

1. **Job catalogue** — the fixed set of named jobs the system knows how
 to run.
2. **Schedules** — operator-configured cron entries that fire jobs.
3. **Run history** — every job invocation logged with status, duration,
 result, and any error.

A fourth piece, the **optimization queue**, lives here too: rules with a
`queue_optimization` action append items to a queue here for 's
optimization workers to consume.

## Built-in jobs

| Key | What it does |
|---------------------------|-----------------------------------------------------------|
| `scan_library` | Walks a library and runs ffprobe on media files |
| `evaluate_library` | Re-runs every enabled rule against every file in a library |
| `healthcheck_integration` | Verifies one integration is reachable, persists status |
| `sync_integration_tags` | Pulls tags from an integration into `media_tags` |

Each job declares its required arguments — for example, `scan_library`
needs a `library_id`. The catalogue is the same surface the API uses,
the schedule editor reads from, and the scheduler dispatches through.

## Cron specs

Schedules use a small JSON document instead of a crontab string:

```json
{ "minute": 0, "hour": 3 } // every day at 03:00
{ "minute": 0, "hour": [2, 14] } // 02:00 and 14:00 every day
{ "minute": 0, "hour": 9, "weekday": 0 } // 09:00 every Monday
{} // every minute (rarely useful)
```

Supported keys: `minute` (0–59), `hour` (0–23), `day` (1–31), `month`
(1–12), `weekday` (0–6, Monday=0). Each can be an int or a list of ints.
Absent keys mean "any".

Times are evaluated in UTC. The frontend renders `next_run_at` and
`last_run_at` in the operator's local timezone but the persisted values
are always UTC.

## How jobs run

The worker (`docker compose --profile worker up -d`) runs a 1-minute
cron loop that calls `Scheduler.tick()`. The tick finds schedules whose
`next_run_at` has passed, dispatches each through the catalogue, writes
a `JobRun` row with `status="running"`, executes, and updates the row
to `completed` or `failed`. Whether or not the underlying work
succeeded, `next_run_at` is always re-primed so a single failing run
can't stall a schedule forever.

You can also run a job immediately:

- From the Automation page: **Run now** on a schedule.
- Via API: `POST /api/v1/automation/run` with `{job_kind, job_args}`.
- Or trigger a specific schedule: `POST /api/v1/automation/schedules/{id}/run`.

All three paths produce the same `JobRun` shape.

## Optimization queue

When the rules engine evaluates a file and a matched rule has a
`queue_optimization` action, the rules service writes an
`OptimizationItem` keyed by `(media_file_id, profile)`. 's
optimization workers will consume from this queue.

Re-evaluating a rule that already queued a file doesn't duplicate the
item — the `(file, profile)` pair is unique. If the queue entry is
already `running` or `completed`, rule re-evaluation leaves it alone.
The Audit trail records which rule first queued each item.

## What's deferred to 

The queue *exists* and accepts items today, but consumption of those
items is . The Optimization page in the UI lists queued items
read-only; transitions between `queued → running → completed/failed`
will come from optimization workers, not the rules engine.
