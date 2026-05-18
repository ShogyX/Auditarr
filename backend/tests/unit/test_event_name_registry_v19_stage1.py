"""Event-name registry contract (v1.9 Stage 1.4).

Pins three properties of the constant-driven event vocabulary in
``app.events.types``:

  1. Every constant value matches the canonical regex
     ``^[a-z]+\\.[a-z_]+(\\.[a-z_]+)?$``.
  2. ``EVENT_NAMES`` contains exactly the values of the public
     constants in the module (no drift between the constants and
     the tuple).
  3. The constants are pure strings (importable, no side effects).

This test exists to catch the failure mode where someone adds a new
event but forgets to wire it into ``EVENT_NAMES``, or vice versa.
"""

from __future__ import annotations

import re

from app.events import types as event_types

_NAME_RE = re.compile(r"^[a-z]+\.[a-z_]+(\.[a-z_]+)?$")


def _public_constants() -> dict[str, str]:
    """Return ``{name: value}`` for every public uppercase constant in
    ``app.events.types`` that holds a string. Excludes ``EVENT_NAMES``
    (a tuple), ``EventName`` (a type alias), and the ``DomainEvent``
    class. Excludes private dunder attributes.
    """
    out: dict[str, str] = {}
    for attr in dir(event_types):
        if attr.startswith("_"):
            continue
        if attr == "EVENT_NAMES":
            continue
        val = getattr(event_types, attr)
        if isinstance(val, str) and attr.isupper():
            out[attr] = val
    return out


def test_every_event_constant_matches_canonical_regex() -> None:
    constants = _public_constants()
    assert constants, "expected at least one event-name constant"
    for name, value in constants.items():
        assert _NAME_RE.match(value), (
            f"constant {name}={value!r} doesn't match {_NAME_RE.pattern}"
        )


def test_event_names_tuple_matches_constants_exactly() -> None:
    """The ``EVENT_NAMES`` tuple must contain exactly the values of the
    module's string constants — no extra entries, no missing entries.

    This is the safety net against adding ``MEDIA_FOO = "media.foo"``
    and forgetting to add it to ``EVENT_NAMES``, or vice versa.
    """
    constants_values = set(_public_constants().values())
    tuple_values = set(event_types.EVENT_NAMES)
    missing_from_tuple = constants_values - tuple_values
    missing_from_constants = tuple_values - constants_values
    assert not missing_from_tuple, (
        f"these constants exist but are not in EVENT_NAMES: "
        f"{sorted(missing_from_tuple)}"
    )
    assert not missing_from_constants, (
        f"these are in EVENT_NAMES but have no module constant: "
        f"{sorted(missing_from_constants)}"
    )


def test_event_names_tuple_has_no_duplicates() -> None:
    names = list(event_types.EVENT_NAMES)
    assert len(names) == len(set(names)), (
        f"EVENT_NAMES has duplicates: "
        f"{[n for n in names if names.count(n) > 1]}"
    )


def test_well_known_events_are_registered() -> None:
    """Sanity check: a handful of names actually emitted in the
    codebase today must be present. If any of these go missing, the
    grep audit that populated this list found a real regression.
    """
    expected = {
        "scan.started",
        "scan.progress",
        "scan.completed",
        "scan.failed",
        "scan.reaped",
        "media.added",
        "media.deleted",
        "media.removed",
        "media.reprobed",
        "rule.matched",
        "rule.throttled",
        "optimization.started",
        "optimization.completed",
        "optimization.failed",
        "optimization.routed",
        "notification.sent",
        "notification.failed",
        "integration.health_changed",
        "integration.tags_synced",
        "integration.path_drift",
        "update.available",
        "update.installed",
        "update.failed",
        "system.startup",
        "system.shutdown",
        "system.user_registered",
        "system.hwaccel_missing",
        "plugin.loaded",
        "plugin.unloaded",
        "plugin.error",
        "virustotal.result",
        "virustotal.quota_exhausted",
    }
    registered = set(event_types.EVENT_NAMES)
    missing = expected - registered
    assert not missing, f"missing well-known event names: {sorted(missing)}"
