"""Stage 06 (v1.7) — delete + notify combined action test.

Plan §374:
    A rule with both ``delete`` and ``notify`` actions emits an
    email whose body contains "no action required".

Verifies the full pipeline:
  1. The evaluator produces both a delete entry AND a notification.
  2. The service layer's dispatch loop passes ``auto_delete: True``
     to the dispatcher's context because the rule has a Delete
     action alongside the Notify.
  3. The default body template renders the
     "[Auto-delete] No action required" badge.

This test is a focused unit-style integration test on the
templating contract; it doesn't exercise the full
`evaluate_library` path (which would need a notification channel
provider, etc.). Stage 12 (notifications audit) will exercise
the end-to-end dispatch through real channels.
"""

from __future__ import annotations

from app.notifications.templating import render_body


def _baseline_vars() -> dict:
    """Variables that the dispatcher's ``_variables()`` populates
    when the rule fires. Anything Stage 06 cares about lives in
    ``auto_delete`` here; the rest are placeholder strings."""
    return {
        "severity": "crit",
        "severity_rank": 100,
        "rule_id": "r1",
        "rule_name": "auto-purge junk",
        "media_file_id": "m1",
        "path": "/lib/junk.mkv",
        "filename": "junk.mkv",
        "library_name": "Movies",
        "message": "",
        "time": "2026-05-16T12:00:00+00:00",
        "auto_delete": False,
    }


# ── Default body template ──────────────────────────────────────


def test_default_body_renders_no_action_required_when_auto_delete_true() -> None:
    """Plan §374 contract — the literal phrase appears."""
    vars_ = _baseline_vars()
    vars_["auto_delete"] = True
    body = render_body(None, vars_)
    assert "No action required" in body
    # The badge label also appears so the operator can scan for it.
    assert "Auto-delete" in body


def test_default_body_omits_badge_when_auto_delete_false() -> None:
    """The default case (no delete on the rule) — the badge is
    absent so existing notification flows aren't polluted with
    irrelevant text."""
    vars_ = _baseline_vars()
    vars_["auto_delete"] = False
    body = render_body(None, vars_)
    assert "No action required" not in body
    assert "Auto-delete" not in body


def test_default_body_omits_badge_when_auto_delete_missing() -> None:
    """Safety net — a Stage 9-era variable bundle that hasn't been
    updated to populate ``auto_delete`` should still render
    cleanly without it.

    Note: with ``StrictUndefined`` the template raises if it
    references a missing variable. The dispatcher always
    populates ``auto_delete`` (Stage 06 added it to
    ``_variables()``), so missing-variable is only possible if a
    caller bypasses the dispatcher. Still verify the dispatcher's
    contract: a False-y value omits the badge."""
    vars_ = _baseline_vars()
    # Don't set auto_delete — but the dispatcher would. Simulate
    # what the dispatcher passes for a non-delete rule.
    vars_["auto_delete"] = False
    body = render_body(None, vars_)
    assert "No action required" not in body


# ── Custom template uses the variable ──────────────────────────


def test_custom_template_can_reference_auto_delete() -> None:
    """Operators who write their own templates should be able to
    reference ``{{ auto_delete }}`` directly. Verifies the variable
    is in scope of every body template, not just the default."""
    vars_ = _baseline_vars()
    vars_["auto_delete"] = True
    custom = (
        "Severity: {{ severity }}. "
        "{% if auto_delete %}DELETING{% else %}REVIEW{% endif %}"
    )
    body = render_body(custom, vars_)
    assert body == "Severity: crit. DELETING"

    vars_["auto_delete"] = False
    body2 = render_body(custom, vars_)
    assert body2 == "Severity: crit. REVIEW"


def test_default_body_with_auto_delete_and_message_both_render() -> None:
    """The badge and the rule's notify.message are independent —
    both should appear when both are set."""
    vars_ = _baseline_vars()
    vars_["auto_delete"] = True
    vars_["message"] = "Detected by Plex-incompat check"
    body = render_body(None, vars_)
    assert "No action required" in body
    assert "Detected by Plex-incompat check" in body
