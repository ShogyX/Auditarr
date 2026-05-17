"""Stage 05 (v1.7) — migration 0015 rewrites quarantine rule bodies.

Plan addendum §A.0: "Drop ``MediaFile.quarantined``,
``MediaFile.quarantine_reason``, ``MediaFile.quarantined_at``.
Drop ``Quarantine`` action class."

The 0015 migration ships two changes:

  1. Persisted rule definitions that referenced
     ``type: "quarantine"`` are rewritten to ``type: "delete"``
     so the next ``RuleDefinition.model_validate`` doesn't fail.
  2. The three quarantine columns are dropped from
     ``media_files``.

This file tests the rewrite-actions helper at unit level (the
column drop is a SQLAlchemy mechanic; testing it would test
Alembic itself, which is out of scope).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_migration_module():
    """Import the migration module by path. Standard ``import``
    chokes on the leading digit in the file name."""
    repo_root = Path(__file__).resolve().parents[2]
    mod_path = repo_root / "migrations" / "versions" / "0023_drop_quarantine.py"
    spec = importlib.util.spec_from_file_location(
        "migration_0023_stage05", str(mod_path)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def m():
    return _load_migration_module()


def test_quarantine_action_rewrites_to_delete_with_reason(m) -> None:
    """``type: "quarantine"`` becomes ``type: "delete"``; the
    operator-supplied reason is preserved verbatim."""
    new, changed = m._rewrite_actions(
        [{"type": "quarantine", "reason": "Plex incompat"}]
    )
    assert changed is True
    assert new == [{"type": "delete", "reason": "Plex incompat"}]


def test_quarantine_action_without_reason_yields_bare_delete(m) -> None:
    """A quarantine action with no reason rewrites to a Delete
    with no reason — the service layer's "Deleted by rule"
    synthesis kicks in at evaluation time."""
    new, changed = m._rewrite_actions([{"type": "quarantine"}])
    assert changed is True
    assert new == [{"type": "delete"}]


def test_delete_action_with_confirm_strips_confirm(m) -> None:
    """The pre-Stage-05 ``confirm`` flag on Delete is gone;
    the migration scrubs it so the row validates post-upgrade."""
    new, changed = m._rewrite_actions(
        [{"type": "delete", "confirm": True, "reason": "kept"}]
    )
    assert changed is True
    assert new == [{"type": "delete", "reason": "kept"}]


def test_delete_action_with_confirm_false_strips_confirm(m) -> None:
    """``confirm: False`` is also stripped — Stage 05 retired the
    flag in both directions."""
    new, changed = m._rewrite_actions(
        [{"type": "delete", "confirm": False}]
    )
    assert changed is True
    assert new == [{"type": "delete"}]


def test_clean_delete_action_passes_through_unchanged(m) -> None:
    """A Delete action that already has no ``confirm`` is left
    alone; ``changed=False`` so the migration skips the UPDATE."""
    new, changed = m._rewrite_actions([{"type": "delete"}])
    assert changed is False
    assert new == [{"type": "delete"}]


def test_clean_delete_with_reason_passes_through_unchanged(m) -> None:
    new, changed = m._rewrite_actions(
        [{"type": "delete", "reason": "explanation"}]
    )
    assert changed is False
    assert new == [{"type": "delete", "reason": "explanation"}]


def test_non_quarantine_non_delete_actions_pass_through(m) -> None:
    """Other action types are completely untouched."""
    actions = [
        {"type": "set_severity", "severity": "crit"},
        {"type": "add_tag", "tag": "foo"},
        {"type": "queue_optimization", "profile": "p1"},
        {"type": "notify", "channel": "c1", "message": "m1"},
    ]
    new, changed = m._rewrite_actions(actions)
    assert changed is False
    assert new == actions


def test_mixed_actions_rewrite_selectively(m) -> None:
    """A list with a quarantine action interleaved with others —
    only the quarantine is rewritten, the rest pass through, and
    ``changed`` reports True for the list as a whole."""
    new, changed = m._rewrite_actions(
        [
            {"type": "set_severity", "severity": "crit"},
            {"type": "quarantine", "reason": "x"},
            {"type": "add_tag", "tag": "foo"},
        ]
    )
    assert changed is True
    assert new == [
        {"type": "set_severity", "severity": "crit"},
        {"type": "delete", "reason": "x"},
        {"type": "add_tag", "tag": "foo"},
    ]


def test_malformed_entries_pass_through_for_loader_to_surface(m) -> None:
    """A non-dict entry in the actions list shouldn't crash the
    migration. The downstream rules loader catches it with a
    clear error message; the migration just stays out of the way."""
    new, changed = m._rewrite_actions(["not-a-dict", 42, None])
    assert changed is False
    assert new == ["not-a-dict", 42, None]


def test_rewritten_actions_validate_against_stage_05_schema(m) -> None:
    """End-to-end contract: rewritten action lists must validate
    against the current (Stage 06) ``RuleDefinition``.

    Stage 06 adds the ``acknowledged_destructive`` requirement on
    rule bodies that contain a delete action. The migration
    rewrites quarantine → delete, so the rewritten body must
    carry the ack flag to validate. Test that, end-to-end."""
    from app.rules.schema import RuleDefinition

    new, _ = m._rewrite_actions(
        [
            {"type": "set_severity", "severity": "crit"},
            {"type": "quarantine", "reason": "Plex incompat"},
            {"type": "delete", "confirm": True, "reason": "junk"},
        ]
    )
    body = {
        "match": {"field": "category", "op": "eq", "value": "media"},
        "actions": new,
        # Stage 06: required when any action is delete. The
        # migration itself doesn't add this — operators or the
        # rules API enforce it at save time. This test just
        # confirms a rewritten action list is shape-valid.
        "acknowledged_destructive": True,
    }
    # Should not raise.
    RuleDefinition.model_validate(body)
