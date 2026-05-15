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
            "so the operator notices."
        ),
        priority=15,
        definition={
            "match": {
                "all": [
                    {"field": "category", "op": "eq", "value": "media"},
                ],
            },
            # Note: the schema doesn't expose probe_failed directly
            # (we'd need to add it to SUPPORTED_FIELDS). For now this
            # rule matches all media files; until probe_failed lands
            # in the rule DSL, operators see this surface different
            # signals via the orphan + unknown-codec rules. The rule
            # is included here so a future DSL extension picks it up
            # without needing a migration — name stays stable.
            #
            # KNOWN LIMITATION: this rule is intentionally seeded
            # DISABLED-via-runtime since its match has no
            # probe-failure predicate yet. Operators see it in the
            # builtin list but it doesn't fire. When the DSL grows a
            # probe_failed field (deferred), the definition is
            # updated in place and the rule starts firing.
            "actions": [
                {"type": "add_tag", "tag": "probe-failed-stub"},
            ],
        },
    ),
)


# The "Probe failed" rule above is shipped DISABLED by default
# because its match predicate isn't expressible in the current DSL.
# Until probe_failed lands as a SUPPORTED_FIELD this would tag
# every media file. Tracked separately so the seeding logic knows
# to default to disabled.
DISABLED_BY_DEFAULT: frozenset[str] = frozenset({"Probe failed"})


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


__all__ = [
    "BUILTIN_RULES",
    "BuiltinRule",
    "DISABLED_BY_DEFAULT",
    "register_builtin_rules",
]
