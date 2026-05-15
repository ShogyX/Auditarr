---
title: Webhook ingress
category: integrations
tags: [webhooks, sonarr, radarr, plex, jellyfin, integrations, setup]
summary: Push-based notifications from Sonarr / Radarr / Plex / Jellyfin so Auditarr reacts to file events in seconds, not poll-intervals.
help_context: [integrations.webhooks, settings.webhooks]
related: [integrations/sonarr, integrations/radarr, integrations/plex, integrations/jellyfin]
---

# Webhook ingress

By default, Auditarr **polls** each integration on its
configured `poll_interval_seconds`. Push-based notifications via
webhooks shorten the gap between an upstream file event (a new
download, a rename, a deletion) and Auditarr's view of the file —
seconds instead of poll-intervals.

This page walks through the per-service setup. The Auditarr side
of the configuration is one button per integration; the upstream
side is service-specific.

## How it works

1. Generate a per-integration **webhook secret** in Auditarr.
2. Paste the secret + the webhook URL into the upstream service's
   webhook configuration.
3. The upstream signs each webhook payload with HMAC-SHA256.
4. Auditarr verifies the signature, reads the event type, and
   takes the corresponding action — **reprobe** (for adds /
   renames) or **mark orphaned** (for deletes).

## Auditarr side — generate the secret

1. Open **Settings → Integrations**.
2. Click the integration you want to wire up (must be Sonarr,
   Radarr, Plex, or Jellyfin).
3. In the edit dialog, find the **Webhook receiver** section.
4. Click **Generate / rotate secret**.
5. **Copy the secret immediately.** It is displayed *exactly once*;
   the server stores only an encrypted hash. If you lose it, you
   can generate a new one — but any upstream using the old secret
   will start failing signature verification until reconfigured.

The webhook URL is shown in the same section. The format is:

```
https://<your-auditarr-host>/api/v1/webhooks/<kind>/<integration_id>
```

where `<kind>` is `sonarr` / `radarr` / `plex` / `jellyfin` and
`<integration_id>` is the integration's UUID.

## Path mappings matter

Webhook payloads carry the **upstream's** view of file paths.
Auditarr's scanner sees its own local paths. Make sure the
integration's **Path Mappings** are configured before flipping
on webhooks — otherwise the dispatcher won't know how to
translate `/data/media/movies/foo.mkv` (Sonarr) into
`/mnt/storage/Movies/foo.mkv` (Auditarr).

## Sonarr

1. In Sonarr: **Settings → Connect → + → Webhook**.
2. **Name**: anything descriptive (e.g. `Auditarr`).
3. **Triggers**: enable `On File Import`, `On File Upgrade`,
   `On Rename`, `On Episode File Delete`. Leave `On Health Issue`
   and the others off — Auditarr ignores those.
4. **URL**: paste the webhook URL from Auditarr.
5. **Method**: `POST`.
6. **Username / Password**: leave blank.
7. **Authentication / Header**:
   - Header name: `X-Auditarr-Signature`
   - Header value format: `sha256={{TriggerName}}` — Sonarr doesn't
     compute HMAC natively. Use the Sonarr **Webhook** connection's
     built-in signing if available, or run a proxy that signs the
     payload. **Note**: Sonarr's native Webhook connection does not
     yet support HMAC — Auditarr will 401 raw Sonarr webhooks. A
     companion `signed-webhook` Sonarr custom script that wraps the
     POST is the typical workaround. See the **Caveats** section
     below for details.
8. Click **Test** in Sonarr. Auditarr should return 200 OK.
9. Save.

## Radarr

Same as Sonarr (Radarr's webhook UI is identical). Use the
triggers `On Movie Imported`, `On File Upgrade`, `On Rename`,
`On Movie File Delete`.

## Plex

1. In Plex: **Settings → Webhooks → Add Webhook** (Plex Pass
   required).
2. **URL**: paste the webhook URL from Auditarr.
3. Plex does **not** support custom request headers natively
   either. Use a proxy (e.g. `nginx` with a small Lua snippet, or
   a tiny FastAPI service) that:
   - Receives the raw Plex webhook,
   - Computes HMAC-SHA256 over the body using your secret,
   - Adds the `X-Auditarr-Signature: sha256=<hex>` header,
   - Forwards to Auditarr.
4. Save.

Auditarr only acts on the `library.new` event from Plex. Other
events (`media.play`, `library.on.deck`, etc.) are recognized
but discarded — they're handled by the existing playback poller.

## Jellyfin

1. Install the **Webhook plugin** from Jellyfin's plugin catalog.
2. **Dashboard → Plugins → Webhook → Add Generic Destination**.
3. **Webhook URL**: paste the URL from Auditarr.
4. **Notification Type**: enable `Item Added`, `Item Updated`,
   `Item Removed`. Leave the rest off.
5. **Headers** (advanced section): add
   `X-Auditarr-Signature: sha256={{ HMAC the request body with
   the secret }}`. The Jellyfin webhook plugin does not yet
   support HMAC computation natively either — same proxy
   workaround as Plex.
6. Save.

## Caveats

The big caveat in all four services above is that **none of them
natively compute HMAC** over the request body. Industry practice
for HMAC'd webhooks is to either:

- Run a small signing proxy in front of Auditarr (recommended for
  production), or
- Use a service like [smee.io](https://smee.io) for tinkering,
  recognizing that the secret travels through the proxy.

The HMAC requirement is non-negotiable — without it, the
webhook URL is an open POST endpoint that any internet host
could hit. The 401 on a missing or invalid signature is what
makes the endpoint safe to expose.

A future Stage may add per-service signing-helper scripts under
`scripts/webhook-signers/`. For now, see the
`docs/integrations/sonarr.md` and friends for service-specific
notes.

## What happens on each event

| Service | Event | Action |
|---|---|---|
| Sonarr | `Download` | reprobe (re-read ffprobe metadata + hash) |
| Sonarr | `Rename` | reprobe |
| Sonarr | `EpisodeFileDelete` | mark the file orphaned in Auditarr |
| Radarr | `Download` | reprobe |
| Radarr | `Rename` | reprobe |
| Radarr | `MovieFileDelete` | mark orphaned |
| Plex | `library.new` | reprobe |
| Jellyfin | `ItemAdded` | reprobe |
| Jellyfin | `ItemUpdated` | reprobe |
| Jellyfin | `ItemRemoved` | mark orphaned |

The `Test` event from every service returns 200 OK with no work
done — useful for verifying connectivity from the upstream's UI.

Unknown events return 200 OK with `action: "ignored"` in the
response body. This is intentional — non-200 responses cause most
upstreams to retry, and we don't want a retry storm if Sonarr
adds a new event type before Auditarr learns to recognize it.

## VirusTotal integration

When Auditarr reprobes a file in response to a webhook, it also:

1. Computes the file's SHA-256 hash (chunked, async — no
   request-latency impact).
2. If the VirusTotal integration is enabled (Settings → System →
   VirusTotal) and within daily quota, looks up the hash on
   VirusTotal's free-tier endpoint.
3. Persists the result (counts of malicious / suspicious /
   harmless / undetected detections) next to the hash.

The Files page drawer's **Security** section surfaces both the
hash and the VT result. Files unknown to VirusTotal are tagged
"unknown"; files VT has seen show a quick clean/suspicious/malicious
pill plus a click-through to the full VT report.

No file content is ever uploaded to VirusTotal — only the hash.
The free tier supports hash-lookup only; file submission would
require a paid tier and is explicitly out of scope.

## Troubleshooting

* **All my webhook calls 401.** The most common cause is that the
  upstream isn't signing the body. Verify with `curl`:
  ```bash
  body='{"eventType":"Test"}'
  sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
  curl -X POST -H "X-Auditarr-Signature: sha256=$sig" \
       -H "Content-Type: application/json" -d "$body" \
       https://your-host/api/v1/webhooks/sonarr/<integration-id>
  ```

* **Reprobe runs but the file's data doesn't update.** Check the
  integration's path mappings. A reprobe of an unknown path
  (after remapping) is a no-op — Auditarr won't fall through to
  scanning the filesystem from a webhook.

* **VirusTotal column stays blank.** Confirm:
  1. The integration is enabled (Settings → System → VirusTotal).
  2. The API key secret is set under the VirusTotal API key slot.
  3. The daily quota (default 250) hasn't been exhausted — check
     server logs for `virustotal.quota_exhausted`.
  4. The hash has been computed. Hashing happens on webhook
     reprobe events; legacy files stay unhashed until a fresh
     event arrives.
