"""Notification templating tests."""

from __future__ import annotations

from app.notifications.templating import (
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    render_body,
    render_subject,
)


def _vars(**over):
    base = dict(
        severity="warn",
        severity_rank=40,
        rule_id="r1",
        rule_name="Big files",
        media_file_id="m1",
        path="/data/movies/big.mkv",
        filename="big.mkv",
        library_name="Movies",
        message="extra context",
        time="2026-05-10T22:00:00+00:00",
        # Stage 06 (v1.7): every rendered notification carries
        # ``auto_delete`` — the dispatcher always sets it; for
        # default-body tests where the rule has no delete action,
        # the variable is False and the badge is omitted.
        auto_delete=False,
    )
    base.update(over)
    return base


def test_default_subject_renders() -> None:
    out = render_subject(None, _vars())
    assert "WARN" in out
    assert "Big files" in out


def test_default_body_renders_with_message() -> None:
    out = render_body(None, _vars())
    assert "Big files" in out
    assert "big.mkv" in out
    assert "Movies" in out
    assert "extra context" in out


def test_default_body_omits_message_when_empty() -> None:
    out = render_body(None, _vars(message=""))
    assert "extra context" not in out
    # Other fields should still render.
    assert "big.mkv" in out


def test_override_subject() -> None:
    out = render_subject("ALERT: {{ filename }}", _vars())
    assert out == "ALERT: big.mkv"


def test_broken_template_falls_back_to_default() -> None:
    """A typo in the operator template shouldn't drop the alert."""
    # ``nonexistent`` is not a known variable; StrictUndefined would normally
    # raise. The render functions catch that and fall back to the default.
    out = render_subject("{{ nonexistent }}", _vars())
    expected = render_subject(DEFAULT_SUBJECT, _vars())
    assert out == expected

    out_body = render_body("{{ nonexistent }}", _vars())
    expected_body = render_body(DEFAULT_BODY, _vars())
    assert out_body == expected_body
