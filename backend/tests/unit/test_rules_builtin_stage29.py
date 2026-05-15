"""Stage 29 — Built-in rules definitions are DSL-valid.

These tests don't touch the database. They verify that every
:class:`BuiltinRule` in :data:`BUILTIN_RULES` parses as a valid
:class:`RuleDefinition` — catching DSL typos before the seeding
function ever runs against a real database.

The seeding logic itself has integration tests in
``tests/integration/test_rules_builtin_stage29.py``.
"""

from __future__ import annotations

import pytest

from app.rules.builtin import BUILTIN_RULES, DISABLED_BY_DEFAULT, BuiltinRule
from app.rules.schema import RuleDefinition


def test_at_least_one_builtin_exists() -> None:
    """If this fires, someone emptied the builtin set — that's the
    Stage 29 contract gone. Refuse silently."""
    assert len(BUILTIN_RULES) >= 1


def test_all_builtin_names_are_unique() -> None:
    """Name is the merge key in register_builtin_rules; duplicates
    would silently make the second-listed builtin invisible."""
    names = [b.name for b in BUILTIN_RULES]
    assert len(names) == len(set(names)), (
        f"Duplicate builtin names: "
        f"{[n for n in names if names.count(n) > 1]}"
    )


def test_all_builtin_definitions_parse() -> None:
    """Each builtin must be a syntactically valid rule. Catches
    things like a misspelled field name (e.g. ``video_codecs``) or
    an operator that doesn't apply to the field's type.
    """
    for spec in BUILTIN_RULES:
        # Exception message includes the rule name so the test
        # failure tells the developer which builtin to fix.
        try:
            RuleDefinition.model_validate(spec.definition)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"Builtin rule {spec.name!r} has an invalid definition: {exc}"
            )


def test_disabled_by_default_names_are_subset_of_builtins() -> None:
    """Items in DISABLED_BY_DEFAULT must reference real builtin
    names — otherwise the seed logic would never disable anything
    and the constant is silently dead."""
    builtin_names = {b.name for b in BUILTIN_RULES}
    unknown = DISABLED_BY_DEFAULT - builtin_names
    assert not unknown, (
        f"DISABLED_BY_DEFAULT references unknown builtins: {unknown}"
    )


def test_builtin_dataclass_is_frozen() -> None:
    """BuiltinRule should be hashable + immutable so accidental
    mutation in seeding logic raises rather than silently drifts."""
    spec = BuiltinRule(
        name="Test", description="", priority=100, definition={}
    )
    with pytest.raises(Exception):
        spec.name = "Modified"  # type: ignore[misc]
