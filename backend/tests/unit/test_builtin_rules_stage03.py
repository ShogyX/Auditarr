"""Stage 03 — built-in rule curation.

Plan §235: for each new rule in ``BUILTIN_RULES``, assert that:

  - the ``definition`` validates against ``RuleDefinition.model_validate``
  - the severity (where set) is one of ``SEVERITY_LEVELS``
  - the rule's name is unique
  - "Non-media file extension" is in ``DISABLED_BY_DEFAULT``

The seven names land in the curated set per plan §220–228:

  1. Plex incompatible video codec
  2. Plex incompatible audio codec
  3. Jellyfin incompatible video codec
  4. Jellyfin incompatible audio codec
  5. Likely transcode trigger (4K HEVC 10-bit)
  6. Executable file in library
  7. Non-media file extension

Priorities sit between 35 and 75 with no collisions against the
pre-Stage-03 priority set ``{10, 15, 20, 30, 40, 50, 60}``.
"""

from __future__ import annotations

import pytest

from app.rules.builtin import BUILTIN_RULES, DISABLED_BY_DEFAULT
from app.rules.schema import SEVERITY_LEVELS, RuleDefinition

STAGE_03_RULE_NAMES = frozenset(
    {
        "Plex incompatible video codec",
        "Plex incompatible audio codec",
        "Jellyfin incompatible video codec",
        "Jellyfin incompatible audio codec",
        "Likely transcode trigger (4K HEVC 10-bit)",
        "Executable file in library",
        "Non-media file extension",
    }
)


def test_all_seven_stage_03_rules_are_present() -> None:
    present = {r.name for r in BUILTIN_RULES}
    missing = STAGE_03_RULE_NAMES - present
    assert not missing, f"missing Stage 03 builtins: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(STAGE_03_RULE_NAMES))
def test_stage_03_rule_definition_validates(name: str) -> None:
    rule = next(r for r in BUILTIN_RULES if r.name == name)
    # ``model_validate`` raises on any schema violation. No assertions
    # needed beyond "doesn't raise".
    RuleDefinition.model_validate(rule.definition)


@pytest.mark.parametrize("name", sorted(STAGE_03_RULE_NAMES))
def test_stage_03_rule_severity_is_canonical(name: str) -> None:
    rule = next(r for r in BUILTIN_RULES if r.name == name)
    set_sev_actions = [
        a for a in rule.definition["actions"] if a["type"] == "set_severity"
    ]
    if not set_sev_actions:
        # Tag-only rules (e.g. Non-media file extension) have no
        # set_severity. That's fine — but if they DO have one, it
        # must be canonical.
        return
    for action in set_sev_actions:
        assert action["severity"] in SEVERITY_LEVELS, (
            f"{name}: severity {action['severity']!r} not in {SEVERITY_LEVELS}"
        )


def test_no_duplicate_rule_names() -> None:
    """Plan §235 contract: rule names are unique across BUILTIN_RULES."""
    names = [r.name for r in BUILTIN_RULES]
    assert len(names) == len(set(names)), "duplicate name in BUILTIN_RULES"


def test_non_media_file_extension_is_enabled_after_stage_06() -> None:
    """Stage 03 originally seeded this rule disabled because the
    ``junk`` category didn't exist until Stage 05. Stage 05
    introduced the extension-classifier; Stage 06 (plan §363)
    flips this rule to enabled now that the category is populated.

    This test inverts the Stage 03 assertion: the rule must NOT
    be in DISABLED_BY_DEFAULT post-Stage-06."""
    assert "Non-media file extension" not in DISABLED_BY_DEFAULT


def test_probe_failed_is_enabled_after_stage_06() -> None:
    """Stage 03 left this rule disabled because the DSL didn't
    expose ``probe_failed`` as a field — the rule body was a stub
    that would have tagged every media file. Stage 06 (plan §362)
    added ``probe_failed`` to ``SUPPORTED_FIELDS`` AND flipped
    the rule body to match on the real predicate AND enables
    the rule.

    Inverts the Stage 03 assertion: the rule must NOT be in
    DISABLED_BY_DEFAULT post-Stage-06."""
    assert "Probe failed" not in DISABLED_BY_DEFAULT


def test_stage_03_priorities_span_the_expected_range() -> None:
    """Priorities for the new rules sit in [35, 75] and don't
    collide with the pre-Stage-03 priority set."""
    pre_stage_03_priorities = {10, 15, 20, 30, 40, 50, 60}
    new_priorities = {
        r.priority for r in BUILTIN_RULES if r.name in STAGE_03_RULE_NAMES
    }
    # All new priorities are in [35, 75]
    for p in new_priorities:
        assert 35 <= p <= 75, f"priority {p} outside [35, 75]"
    # No collision with existing priorities.
    collision = new_priorities & pre_stage_03_priorities
    assert not collision, f"priority collision with existing rules: {collision}"


def test_executable_rule_uses_dotless_extensions() -> None:
    """The scanner stores ``extension`` lowercased and dotless
    (``abs_path.suffix.lstrip(".").lower()``). The rule's value
    list must match — a stored row never carries the leading dot."""
    rule = next(
        r for r in BUILTIN_RULES if r.name == "Executable file in library"
    )
    ext_cond = next(
        c
        for c in rule.definition["match"]["all"]
        if c["field"] == "extension"
    )
    for v in ext_cond["value"]:
        assert not v.startswith("."), (
            f"executable rule value {v!r} carries a leading dot; "
            "storage is dotless"
        )
        assert v == v.lower(), f"executable rule value {v!r} not lowercased"


def test_plex_video_codec_list_includes_expected_universals() -> None:
    """Plan §220: msmpeg4v3, wmv3, mpeg2video, mpeg4, theora, mjpeg."""
    rule = next(
        r for r in BUILTIN_RULES if r.name == "Plex incompatible video codec"
    )
    codec_cond = next(
        c
        for c in rule.definition["match"]["all"]
        if c["field"] == "video_codec"
    )
    assert set(codec_cond["value"]) == {
        "msmpeg4v3",
        "wmv3",
        "mpeg2video",
        "mpeg4",
        "theora",
        "mjpeg",
    }


def test_jellyfin_video_codec_list_includes_expected_universals() -> None:
    """Plan §222: wmv3, msmpeg4v3, mpeg4, theora, mjpeg."""
    rule = next(
        r
        for r in BUILTIN_RULES
        if r.name == "Jellyfin incompatible video codec"
    )
    codec_cond = next(
        c
        for c in rule.definition["match"]["all"]
        if c["field"] == "video_codec"
    )
    assert set(codec_cond["value"]) == {
        "wmv3",
        "msmpeg4v3",
        "mpeg4",
        "theora",
        "mjpeg",
    }


def test_transcode_trigger_uses_width_and_height_thresholds() -> None:
    """Plan §224: 4K HEVC 10-bit. Match must include ``hevc`` codec
    AND width >= 3000 AND height >= 1600."""
    rule = next(
        r
        for r in BUILTIN_RULES
        if r.name == "Likely transcode trigger (4K HEVC 10-bit)"
    )
    conds = rule.definition["match"]["all"]
    codec = next(c for c in conds if c["field"] == "video_codec")
    width = next(c for c in conds if c["field"] == "width")
    height = next(c for c in conds if c["field"] == "height")
    assert codec["value"] == "hevc"
    assert width["op"] == "gte" and width["value"] >= 3000
    assert height["op"] == "gte" and height["value"] >= 1600
