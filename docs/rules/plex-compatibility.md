---
id: rules/plex-compatibility
title: Plex direct-play compatibility — the honest story
category: rules
tags: [rules, plex, compatibility, transcode, direct-play]
summary: Plex direct-play compatibility varies by client. The built-in compatibility rule flags codecs that fail on most clients, not all clients.
help_context: [rules.plex-compatibility]
related: [rules/conditions, integrations/plex]
---

# Plex direct-play compatibility

> **Honest disclaimer.** Plex direct-play compatibility is
> **client-dependent**, not server-dependent. The same file
> direct-plays on a Roku Ultra, transcodes on an LG smart TV,
> and falls back to direct-stream on a Chromecast. There is
> no universally-incompatible codec for Plex's whole client
> matrix.
>
> Auditarr's built-in **Plex transcode compatibility** rule
> flags codecs that **fail on the majority of Plex clients**.
> Treat its output as "here are files most clients won't
> play smoothly" — *not* as "here are files Plex can't
> play".

## What the built-in rule actually checks

The rule (shipped as a built-in in) matches on
`video_codec` and `audio_codec`. The current trigger list,
chosen for the **majority of clients** (Chromecast, smart TV
apps, mobile, Plex Web on older browsers):

**Video** that most clients can't direct-play:
- `av1` (very low client support outside latest mobile / TV apps)
- `vp9` (Plex Web direct-plays, most native clients transcode)
- `prores` / `dnxhd` (intermediate codecs, no client support)
- `mpeg2video` in containers other than TS / MPG

**Audio** that most clients can't direct-play:
- `truehd` (TV apps transcode to AC3)
- `dts-hd ma`, `dts-x` (lossless DTS variants, narrow support)
- `eac3` with `> 6 channels` (mobile apps fall back to stereo
 mix-down, which is technically a transcode)

The rule does **not** flag `h264` / `hevc` / `aac` / `ac3` /
`flac` — these direct-play on the broad majority of clients.

## Per-client reality

| Client | Notable compat quirks |
|---|---|
| Plex Web (Chromium) | h264/hevc/vp9/aac/ac3/flac all direct-play; av1 transcodes; most subtitle formats burn-in. |
| Plex Web (Safari) | Roughly the same as Chromium; HEVC depends on hardware; some MP4 containers prefer remux. |
| Roku Ultra | Direct-plays almost everything except TrueHD audio (transcodes to AC3); subtitle handling is solid. |
| Apple TV (tvOS) | Direct-plays h264/hevc/aac/ac3/eac3; av1 transcodes; PGS subtitles burn-in. |
| Smart TVs (LG / Samsung / Sony) | Highly variable per model + year. Older models transcode HEVC; newer models direct-play. Best to test per-model. |
| Mobile (iOS / Android) | Generally direct-plays h264/aac; transcodes hevc on older devices; multichannel audio mixes down to stereo. |
| Chromecast (Google TV) | Direct-plays h264/hevc/aac/ac3; vp9 direct-plays; av1 needs Chromecast 4K Google TV or newer; subtitles often burn-in. |

This table is **indicative, not authoritative** — Plex
publishes client matrices that change with every Plex Server
and client release. Always test your own files on your own
clients before treating the rule's output as final.

## Why the rule still earns its place

Even though the rule is approximate, it surfaces files that
are likely to cause transcode load on your Plex server. For
operators running Plex on a low-power host (a NAS, a small
ARM box, a shared Synology), reducing the transcode-prone
files in the library is a genuine win. The rule's job is
"here are the candidates worth re-encoding" — not "here are
files Plex can't play".

## Tuning the rule for your client mix

If your library is consumed primarily by one client (e.g.
a single Apple TV in the living room), the codec list above
is likely too broad. To narrow it:

1. **Edit the rule** in Settings → Rules → "Plex transcode
 compatibility". The visual editor exposes the
 `video_codec` / `audio_codec` condition.
2. **Remove codecs your client direct-plays.** For an
 Apple-TV-only setup, remove `vp9` from the video list
 (newer Apple TVs handle it). For a Roku-only setup,
 remove `eac3` from the audio list.
3. **Add codecs your client doesn't handle**, if any. The
 rule's flag list isn't exhaustive — it's a starting
 point.

The dry-run preview shows you exactly which files
the new conditions would flag before you save. Use it to
sanity-check that the narrowed rule isn't dragging in files
that play fine on your setup.

## Disabling the rule entirely

If you've decided this rule isn't useful for your library
(e.g. your only client is Plex Web on a powerful laptop and
nothing transcodes), disable it in Settings → Rules. Disabled
rules don't fire on scans; the column will go silent.

## Related

- [rules/conditions](conditions) — the condition vocabulary
 the rule uses.
- [rules/actions](actions) — what actions a rule can take.
- [integrations/plex](../integrations/plex) — wiring your
 Plex server to Auditarr.
