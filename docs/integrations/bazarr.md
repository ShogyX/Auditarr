---
id: integrations/bazarr
title: Bazarr integration
category: integrations
tags: [bazarr, integrations, subtitles]
summary: Surface missing-subtitle gaps from Bazarr as Auditarr tags.
help_context: [integrations.bazarr]
related: [integrations/sonarr, integrations/radarr, integrations/overview]
---

# Bazarr integration

Bazarr follows Sonarr and Radarr to manage subtitles for the same media.
Auditarr uses Bazarr primarily as a **signal source** — every series or
movie Bazarr tracks reports its currently-missing subtitle languages,
which lets you write rules like "flag any title with no English subtitles".

## What works today

- **Healthcheck** — `GET /api/system/status` returns Bazarr's version and
  whether the upstream Sonarr/Radarr SignalR connections are alive.
- **Library discovery** — returns `[]` deliberately. Bazarr doesn't own
  libraries; it follows Sonarr/Radarr, so promote roots from those
  integrations instead.
- **Tag mirroring** — `GET /api/series` + `GET /api/movies` combined.
  Every title with a `missing_subtitles` list produces tags of the form
  `missing-subs:<lang>` keyed by the title's on-disk path.

## Configuration

| Field                          | Example                        |
|--------------------------------|--------------------------------|
| Server URL                     | `http://bazarr.local:6767`     |
| API key                        | _from Settings → General_      |
| Verify TLS                     | `true`                         |
| Timeout (s)                    | `15`                           |
| Mirror missing-subtitle tags   | `true`                         |

## Tag shape

For a series at `/data/tv/Breaking Bad` missing English and Spanish:

```
(/data/tv/Breaking Bad, missing-subs:en)
(/data/tv/Breaking Bad, missing-subs:es)
```

The rules engine (Stage 6) will consume these as conditions.
