"""Built-in rules — seeded at startup (Stage 29).

A small, opinionated set of common audit rules that every Auditarr
installation gets out of the box. They cover situations that show
up across nearly every media library: orphaned files, oversized
remuxes, files without subtitles, ancient codecs that won't direct
play, etc.

# Why builtins exist

A fresh Auditarr installation with zero rules surfaces nothing
useful on the dashboard. Operators have to learn the DSL, write
their first rule, then wait for the next scan to see anything
flagged. That's a poor first run. Builtins make the system useful
the moment it's installed; operators can disable any they don't
want, or duplicate-and-edit any they want a variant of.

# Authority model

Built-in rules are owned by the codebase. At the API layer
(:mod:`app.api.v1.rules`) operators can:

  - Toggle ``enabled`` (turn one off for their installation)
  - Adjust ``priority`` (re-order without renaming)
  - Duplicate to create a writable custom variant

They cannot:

  - Rename or edit the body of a builtin (the codebase owns
    the canonical definition; an operator's local edit would be
    overwritten on the next startup re-seed anyway)
  - Delete a builtin (turn it off via ``enabled`` instead)

# Idempotency contract

:func:`register_builtin_rules` runs on every app startup. It must
be safe to call repeatedly — a restart shouldn't duplicate rules,
shouldn't clobber operator-driven ``enabled`` / ``priority``, and
shouldn't reset evaluation history. The function keys on rule
``name`` (which is unique) and applies a careful merge:

  - If a builtin with that name doesn't exist → INSERT.
  - If it exists and ``is_builtin=True`` → UPDATE only
    ``description`` and ``definition`` (the codebase-owned
    fields). Leave ``enabled``, ``priority``, ``last_*`` alone.
  - If it exists with ``is_builtin=False`` (operator collision —
    they created a custom rule with the same name as a future
    builtin we now want to ship) → leave the row untouched and
    log a warning. The operator's rule wins; we never silently
    promote a custom rule to builtin.

# Future extensions

Adding a new builtin: append a :class:`BuiltinRule` to
:data:`BUILTIN_RULES`. The next startup picks it up. Don't
rename existing builtins; that's effectively a delete-and-add
from the persistence layer's perspective and operators' tweaks
to the old row would be orphaned. If you must rename, do so via
a data migration that preserves the row id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import Rule
from app.services.repositories import RuleRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuiltinRule:
    """Codebase-owned rule definition.

    ``name`` is the unique key; ``description`` and ``definition``
    are the codebase-owned fields that get refreshed on every
    startup. ``priority`` is a default — operators can override it.
    """

    name: str
    description: str
    priority: int
    definition: dict[str, Any]


# ── Curated builtin set ─────────────────────────────────────────
#
# Conservative on purpose: these should be uncontroversial wins
# that almost every library benefits from. Operators can disable
# any they don't want. Adding a new builtin is a deliberate
# decision — anything that would surprise an operator should be a
# suggestion (Stage 16) instead.
#
# Ordering is rough but stable: severity-elevation rules first,
# then tagging rules, then queue-optimization rules. The numeric
# priority spaces them out so operators can insert custom rules
# between them.

BUILTIN_RULES: tuple[BuiltinRule, ...] = (
    BuiltinRule(
        name="Orphaned files",
        description=(
            "Flag files the scanner can no longer find on disk. "
            "Orphans usually mean a media file was moved, renamed, "
            "or deleted outside Auditarr. Surfacing them as 'warn' "
            "lets the operator decide whether to remove the row."
        ),
        priority=10,
        definition={
            "match": {
                "all": [
                    {"field": "is_orphaned", "op": "eq", "value": True},
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": "orphaned"},
            ],
        },
    ),
    BuiltinRule(
        name="Unknown video codec",
        description=(
            "Media file whose probe didn't identify a video codec. "
            "Usually means the probe failed, the file is corrupt, "
            "or it's an audio-only file misclassified as video."
        ),
        priority=20,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {"field": "video_codec", "op": "eq", "value": None},
                    {"field": "is_orphaned", "op": "eq", "value": False},
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "info"},
                {"type": "add_tag", "tag": "no-video-codec"},
            ],
        },
    ),
    BuiltinRule(
        name="Legacy video codec (MPEG-2 / MPEG-4 Part 2)",
        description=(
            "Files using codecs that predate H.264. Almost no modern "
            "client direct-plays these efficiently. Tag for review; "
            "do not auto-queue optimization (the operator may have a "
            "deliberate reason — e.g. authentic vintage rips)."
        ),
        priority=30,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "field": "video_codec",
                        "op": "in",
                        "value": ["mpeg2video", "mpeg4", "msmpeg4v3", "wmv3"],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "info"},
                {"type": "add_tag", "tag": "legacy-codec"},
            ],
        },
    ),
    BuiltinRule(
        name="Very high bitrate (>40 Mbps)",
        description=(
            "Media files above 40 Mbps. Almost always a remux or an "
            "uncompressed transfer that could shrink dramatically "
            "with little quality loss. Flagged 'info' rather than "
            "'warn' because some operators deliberately keep remuxes."
        ),
        priority=40,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {"field": "bitrate_kbps", "op": "gt", "value": 40_000},
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "info"},
                {"type": "add_tag", "tag": "very-high-bitrate"},
            ],
        },
    ),
    BuiltinRule(
        name="Missing subtitles (English audio)",
        description=(
            "English-language media with no subtitle track. Doesn't "
            "elevate severity — many operators don't want subtitles "
            "on everything — but tags so the operator can find them "
            "easily if they do want to bulk-fix."
        ),
        priority=50,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {"field": "has_subtitles", "op": "eq", "value": False},
                    {
                        "field": "audio_languages",
                        "op": "contains",
                        "value": "eng",
                    },
                ],
            },
            "actions": [
                {"type": "add_tag", "tag": "no-subtitles"},
            ],
        },
    ),
    BuiltinRule(
        name="Very small media file (<10 MB)",
        description=(
            "Media files under 10 MB. Often sample files, trailers, "
            "or extras that ended up in the main library directory. "
            "Tag for review."
        ),
        priority=60,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {"field": "size_bytes", "op": "lt", "value": 10_485_760},
                ],
            },
            "actions": [
                {"type": "add_tag", "tag": "tiny-file"},
            ],
        },
    ),
    BuiltinRule(
        name="Probe failed",
        description=(
            "Media files where ffprobe returned an error. Usually "
            "means a corrupt or truncated file. Elevated to 'warn' "
            "so the operator notices. Enabled by default in Stage 06 "
            "(v1.7) now that ``probe_failed`` is a DSL field."
        ),
        priority=15,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    # Stage 06 (v1.7): the rule's match predicate
                    # finally has its missing piece. Pre-Stage-06
                    # this rule was a stub (matched every media
                    # file, tagged ``probe-failed-stub``) and
                    # shipped DISABLED-by-default. Stage 06 added
                    # ``probe_failed`` to ``SUPPORTED_FIELDS`` /
                    # ``BOOL_FIELDS`` so the rule can fire on the
                    # actual condition.
                    {"field": "probe_failed", "op": "eq", "value": True},
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": "probe-failed"},
            ],
        },
    ),
    # ── Stage 03 (v1.7) — Plex / Jellyfin compatibility, executables, junk
    # ──
    # Codec lists are honest about scope: they flag codecs known to
    # be universally unsupported by the named client (not "every
    # codec the web client doesn't direct-play"). See addendum B.2 —
    # Plex direct-play compat is client-dependent; this rule's
    # description acknowledges that.
    BuiltinRule(
        name="Plex incompatible video codec",
        description=(
            "Detects video codecs that Plex transcoding fails to "
            "direct-play on most clients. Plex direct-play "
            "compatibility varies by client; this rule flags codecs "
            "that are universally unsupported (msmpeg4v3, wmv3, "
            "mpeg2video, mpeg4, theora, mjpeg). Tagged "
            "'plex-incompatible-video' for operator triage."
        ),
        priority=35,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "field": "video_codec",
                        "op": "in",
                        "value": [
                            "msmpeg4v3",
                            "wmv3",
                            "mpeg2video",
                            "mpeg4",
                            "theora",
                            "mjpeg",
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "plex-incompatible-video"},
            ],
        },
    ),
    BuiltinRule(
        name="Plex incompatible audio codec",
        description=(
            "Audio codecs that Plex cannot direct-play on most "
            "clients (truehd, dts-hd, dts:x, atrac3, wmav2, speex). "
            "Tagged 'plex-incompatible-audio' for operator triage."
        ),
        priority=45,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "field": "audio_codec",
                        "op": "in",
                        "value": [
                            "truehd",
                            "dts-hd",
                            "dts:x",
                            "atrac3",
                            "wmav2",
                            "speex",
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "plex-incompatible-audio"},
            ],
        },
    ),
    BuiltinRule(
        name="Jellyfin incompatible video codec",
        description=(
            "Video codecs Jellyfin transcoding struggles with across "
            "the common clients (wmv3, msmpeg4v3, mpeg4, theora, "
            "mjpeg). Tagged 'jellyfin-incompatible-video'."
        ),
        priority=55,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "field": "video_codec",
                        "op": "in",
                        "value": [
                            "wmv3",
                            "msmpeg4v3",
                            "mpeg4",
                            "theora",
                            "mjpeg",
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "jellyfin-incompatible-video"},
            ],
        },
    ),
    BuiltinRule(
        name="Jellyfin incompatible audio codec",
        description=(
            "Audio codecs Jellyfin direct-play does not support "
            "(truehd, dts-hd, dts:x, wmav2, speex). Tagged "
            "'jellyfin-incompatible-audio'."
        ),
        priority=65,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "field": "audio_codec",
                        "op": "in",
                        "value": [
                            "truehd",
                            "dts-hd",
                            "dts:x",
                            "wmav2",
                            "speex",
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "jellyfin-incompatible-audio"},
            ],
        },
    ),
    BuiltinRule(
        name="Likely transcode trigger (4K HEVC 10-bit)",
        description=(
            "4K HEVC files almost always force a transcode on "
            "common direct-play targets (older Apple TVs, Chromecast "
            "with Google TV, some Roku models). This rule flags "
            "candidate files so operators considering a downscale "
            "profile can find them. Doesn't elevate to 'crit' "
            "because operators with capable hardware shouldn't be "
            "alarmed."
        ),
        priority=70,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {"field": "video_codec", "op": "eq", "value": "hevc"},
                    {"field": "width", "op": "gte", "value": 3000},
                    {"field": "height", "op": "gte", "value": 1600},
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": "likely-transcode-trigger"},
            ],
        },
    ),
    BuiltinRule(
        name="Executable file in library",
        description=(
            "An executable in a media library is almost always "
            "wrong — a stray installer, a misplaced script, or in "
            "the worst case a malicious payload. The match list "
            "covers the common operating-system executables: exe, "
            "bat, cmd, ps1, sh, com, scr, msi, app, dmg. The "
            "scanner stores extensions WITHOUT a leading dot so "
            "the value list is dotless. Tagged "
            "'executable-in-library' at 'crit' severity."
        ),
        priority=73,
        definition={
            "match": {
                "all": [
                    # Extension storage is dotless (scanner strips
                    # the leading "." — see services/media/scanner.py).
                    {
                        "field": "extension",
                        "op": "in",
                        "value": [
                            "exe",
                            "bat",
                            "cmd",
                            "ps1",
                            "sh",
                            "com",
                            "scr",
                            "msi",
                            "app",
                            "dmg",
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "executable-in-library"},
            ],
        },
    ),
    BuiltinRule(
        name="Non-media file extension",
        description=(
            "Files whose extension marks them as junk (txt, log, "
            "nfo offshoots, leftover .part / .crdownload). Stage 05 "
            "introduces the 'junk' category cleanly via the "
            "extension-classifier; until then the rule sits enabled "
            "but matches nothing because no file is ever categorised "
            "as 'junk'. The contract ships now so a future toggle "
            "doesn't need a code change to start firing."
        ),
        priority=75,
        definition={
            "match": {
                "all": [
                    # Stage 05 introduces the 'junk' category. Until
                    # then this matches zero rows — see
                    # DISABLED_BY_DEFAULT below.
                    {"field": "category", "op": "eq", "value": "junk"},
                ],
            },
            "actions": [
                {"type": "add_tag", "tag": "junk-extension"},
            ],
        },
    ),
    # Stage 06 (v1.7) — VirusTotal-driven rule. Per plan §364:
    # severity ``crit``, matches ``vt_status in [malicious,
    # suspicious]``. The ``vt_status`` field was added to
    # ``SUPPORTED_FIELDS`` in Stage 06; the column on
    # ``MediaFile`` (migration 0024) is populated by the
    # VirusTotal plugin once Stage 10 wires it. Pre-Stage-10
    # this rule matches no rows (the column is NULL for every
    # file), so shipping it enabled is safe.
    BuiltinRule(
        name="VirusTotal non-clean",
        description=(
            "Files where VirusTotal returned malicious or suspicious "
            "verdicts. Escalates to 'crit' so the operator notices "
            "immediately. Populated by the VirusTotal plugin once "
            "configured; matches no files until then."
        ),
        priority=10,  # earliest — VT verdicts should fire before
        # any cosmetic codec rules so the crit severity wins
        # aggregation.
        definition={
            "match": {
                "field": "vt_status",
                "op": "in",
                "value": ["malicious", "suspicious"],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "virustotal-non-clean"},
            ],
        },
    ),
    # ── v1.9 Stage 4.7 — Plex / Jellyfin compat rules ────────────
    #
    # Three rules covering the spectrum of "this might transcode" →
    # "this WILL transcode" → "this won't play at all". PLAN.md
    # described these as templates (per Stage 4.4); shipping them
    # as plain builtins for now since the rule_templates table
    # isn't yet in place. When Stage 4.4 lands, these three rows
    # migrate automatically (same name-keyed seed path as every
    # other builtin).
    #
    # Schema limitations: the v1.9 MediaFile model doesn't carry
    # pix_fmt (for 10-bit detection), HDR/Dolby Vision flags, or
    # detailed audio profile (DTS-HD MA vs DTS regular). We use
    # the fields we DO have (video_codec, audio_codec, container,
    # width, height) to approximate. False negatives are accepted
    # — when ffprobe is extended (a future stage), the rule
    # definitions tighten.
    BuiltinRule(
        name="Likely transcode (Plex/Jellyfin)",
        description=(
            "Files that often force a transcode on common direct-"
            "play targets: HEVC at 1080p+ (older clients lack "
            "hardware decode), AC3 5.1 audio (many browser players "
            "won't decode it natively). Surfacing these helps "
            "operators decide whether to maintain alternate "
            "downscaled copies for those clients."
        ),
        priority=75,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "any": [
                            {
                                "all": [
                                    {"field": "video_codec", "op": "eq", "value": "hevc"},
                                    {"field": "height", "op": "gte", "value": 1080},
                                ],
                            },
                            {"field": "audio_codec", "op": "eq", "value": "ac3"},
                            {"field": "audio_codec", "op": "eq", "value": "eac3"},
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": "likely-transcode"},
            ],
        },
    ),
    BuiltinRule(
        name="Always transcode (Plex/Jellyfin)",
        description=(
            "Files that essentially always trigger a server-side "
            "transcode on Plex/Jellyfin: 4K HEVC content (very few "
            "client devices direct-play 4K HEVC), DTS-HD MA "
            "(approximated as audio_codec 'dts'; most browser "
            "and mobile clients lack the decoder). Elevates to "
            "'high' so the operator notices these clusters when "
            "their server is CPU-bound during peak playback."
        ),
        priority=72,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "any": [
                            {
                                "all": [
                                    {"field": "video_codec", "op": "eq", "value": "hevc"},
                                    {"field": "width", "op": "gte", "value": 3000},
                                    {"field": "height", "op": "gte", "value": 1600},
                                ],
                            },
                            {"field": "audio_codec", "op": "eq", "value": "dts"},
                            {"field": "audio_codec", "op": "eq", "value": "truehd"},
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "high"},
                {"type": "add_tag", "tag": "always-transcode"},
            ],
        },
    ),
    BuiltinRule(
        name="Unplayable / Unsupported (Plex/Jellyfin)",
        description=(
            "Files that won't direct-play on most clients AND will "
            "often fail to transcode cleanly. MPEG-2 video in an "
            "MP4 container is a known incompatibility (the muxer "
            "is wrong); Bink video and Vorbis-in-MP4 are similar "
            "edge cases. Elevates to 'crit' so the operator can "
            "remux/recode before a user hits the failure."
        ),
        priority=15,  # very early — these are real "this is broken"
        # signals, not preference-driven.
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                    {
                        "any": [
                            {
                                "all": [
                                    {"field": "video_codec", "op": "eq", "value": "mpeg2video"},
                                    {"field": "container", "op": "eq", "value": "mov"},
                                ],
                            },
                            {"field": "video_codec", "op": "eq", "value": "bink"},
                            {"field": "video_codec", "op": "eq", "value": "binkvideo"},
                            {
                                "all": [
                                    {"field": "audio_codec", "op": "eq", "value": "vorbis"},
                                    {"field": "container", "op": "eq", "value": "mov"},
                                ],
                            },
                        ],
                    },
                ],
            },
            "actions": [
                {"type": "set_severity", "severity": "crit"},
                {"type": "add_tag", "tag": "unplayable"},
            ],
        },
    ),
)


# Stage 06 (v1.7) — both rules previously DISABLED_BY_DEFAULT
# (per plan §337 / §363) are now enabled:
#   * "Probe failed" — the DSL gained the ``probe_failed`` field,
#     so the rule now matches the actual condition rather than
#     tagging every media file as a stub.
#   * "Non-media file extension" — Stage 05 introduced the
#     extension-classifier which populates the 'junk' category;
#     the rule now matches the rows it was always intended to.
# Both are kept in the builtin set so existing operator
# customisations survive a refresh.
DISABLED_BY_DEFAULT: frozenset[str] = frozenset()


async def register_builtin_rules(session: AsyncSession) -> dict[str, int]:
    """Idempotently seed / refresh the builtin rule set.

    Returns a small dict of counters useful for startup logs and
    the regression tests:

      - ``inserted``: new builtins added this run
      - ``refreshed``: existing builtins whose
        description/definition were updated to the current
        codebase-owned version
      - ``unchanged``: existing builtins whose stored
        description/definition already match the codebase
      - ``conflicts``: existing rows with the same name but
        ``is_builtin=False`` (operator collisions; left alone)

    See module docstring for the merge contract.
    """
    repo = RuleRepository(session)
    inserted = refreshed = unchanged = conflicts = 0

    for spec in BUILTIN_RULES:
        existing = await repo.get_by_name(spec.name)
        if existing is None:
            # New builtin → INSERT.
            rule = Rule(
                name=spec.name,
                description=spec.description,
                # Most builtins ship enabled. Anything in
                # DISABLED_BY_DEFAULT (e.g. the placeholder probe-
                # failed rule) ships disabled until its DSL support
                # lands — see the comment in BUILTIN_RULES.
                enabled=spec.name not in DISABLED_BY_DEFAULT,
                priority=spec.priority,
                definition=spec.definition,
                is_builtin=True,
            )
            await repo.add(rule)
            inserted += 1
            log.info("builtin_rule.inserted", extra={"rule_name": spec.name})
            continue

        if not existing.is_builtin:
            # Operator-created rule with the same name. We don't
            # promote it to builtin (that would silently mutate
            # ownership). Log and skip.
            log.warning(
                "builtin_rule.conflict_with_custom",
                extra={"rule_name": spec.name},
            )
            conflicts += 1
            continue

        # Existing builtin: refresh description + definition only.
        # Leave enabled, priority, last_evaluated_at, and
        # last_match_count alone — those are the operator's to set.
        if (
            existing.description == spec.description
            and existing.definition == spec.definition
        ):
            unchanged += 1
        else:
            existing.description = spec.description
            existing.definition = spec.definition
            refreshed += 1
            log.info("builtin_rule.refreshed", extra={"rule_name": spec.name})

    await session.commit()
    return {
        "inserted": inserted,
        "refreshed": refreshed,
        "unchanged": unchanged,
        "conflicts": conflicts,
    }


# ── v1.9 Stage 4.4 — templates seeder ──────────────────────────


async def register_builtin_templates(session: AsyncSession) -> dict[str, int]:
    """Idempotently seed / refresh the rule TEMPLATES table.

    Stage 4.4 ships every shipped rule as a TEMPLATE in addition
    to the legacy seed-as-builtin-rule path. Templates are
    reference material — they don't evaluate against media on
    their own. Operators see them in a new Templates tab on the
    Rules page; clicking "Use template" inserts a normal
    operator-owned ``Rule`` row whose body is copied from the
    template (Stage 4.4 plan §265).

    Same merge contract as ``register_builtin_rules``:
      - ``inserted``: new templates added this run
      - ``refreshed``: existing templates whose description /
        definition / priority were updated to the current
        codebase version
      - ``unchanged``: existing templates that already match

    No ``conflicts`` counter — the templates table is owned by
    the codebase. The seeder writes by ``name`` as the upsert
    key (the column has a unique index per migration 0028).
    Operators don't author templates, so there's no equivalent
    of the rule-row "operator authored a rule with the same name"
    collision case.

    Runs on every startup. Deleting a row from ``rule_templates``
    is the operator's "I don't want to see this template" gesture;
    the next startup re-seeds it (per plan §266 — "Restore deleted
    built-ins is a single action that resets every template to the
    shipped definition"). To make this work, the seeder treats
    "row missing" as "insert", just like a fresh install.
    """
    from sqlalchemy import select

    from app.models.rule_template import RuleTemplate
    from app.utils.datetime import utcnow

    inserted = refreshed = unchanged = 0
    now = utcnow()

    for spec in BUILTIN_RULES:
        existing = (
            await session.execute(
                select(RuleTemplate).where(RuleTemplate.name == spec.name)
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                RuleTemplate(
                    name=spec.name,
                    description=spec.description,
                    priority=spec.priority,
                    definition=spec.definition,
                    seeded_at=now,
                )
            )
            inserted += 1
            log.info("builtin_template.inserted", extra={"template_name": spec.name})
            continue

        # Refresh description / definition / priority if any drift.
        # Always bump seeded_at on this code path so the operator
        # can tell which startup last touched the row.
        if (
            existing.description == spec.description
            and existing.definition == spec.definition
            and existing.priority == spec.priority
        ):
            unchanged += 1
        else:
            existing.description = spec.description
            existing.definition = spec.definition
            existing.priority = spec.priority
            existing.seeded_at = now
            refreshed += 1
            log.info(
                "builtin_template.refreshed",
                extra={"template_name": spec.name},
            )

    await session.commit()
    return {
        "inserted": inserted,
        "refreshed": refreshed,
        "unchanged": unchanged,
    }


__all__ = [
    "BUILTIN_RULES",
    "BuiltinRule",
    "DISABLED_BY_DEFAULT",
    "register_builtin_rules",
    "register_builtin_templates",
]
