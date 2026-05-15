"""Version comparison.

We intentionally do *not* depend on ``packaging`` or ``semver`` here.
Auditarr versions follow the strict ``MAJOR.MINOR.PATCH[-prerelease]``
shape that comes out of the image-build pipeline, plus the development
sentinel ``0.0.0-dev``. A small purpose-built comparator keeps the
behaviour explicit and avoids surprising prerelease ordering rules.

Comparison rules:

* ``0.0.0-dev`` is considered *older than any released version*. This
  means dev builds always see "update available", which is what we
  want — it nudges contributors to test their changes against the
  shipped release flow rather than staring at "up to date" by accident.
* Pure ``MAJOR.MINOR.PATCH`` compares numerically by tuple.
* When one side has a prerelease tag (``1.2.0-rc.1``), it is older
  than the same version without one (``1.2.0``).
* Comparing two prerelease versions of the same numeric trio falls
  back to lexicographic order of the prerelease tag — good enough for
  ``rc.1 < rc.2`` and "we don't really care about exotic schemes".
"""

from __future__ import annotations

import re

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")
DEV_SENTINEL = "0.0.0-dev"


def parse(version: str) -> tuple[int, int, int, str | None] | None:
    """Parse a version string. Returns None when the shape doesn't match."""
    match = VERSION_RE.match(version.strip())
    if match is None:
        return None
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), pre)


def is_newer(candidate: str, installed: str) -> bool:
    """Return True when ``candidate`` should be considered an upgrade."""
    if installed == DEV_SENTINEL and candidate != DEV_SENTINEL:
        return True
    if candidate == DEV_SENTINEL:
        return False
    a, b = parse(candidate), parse(installed)
    if a is None or b is None:
        # Unknown shape — fall back to "different is upgrade". This is
        # conservative: it surfaces a notification rather than silently
        # missing one when an operator pinned a non-semver tag.
        return candidate != installed
    a_num = a[:3]
    b_num = b[:3]
    if a_num != b_num:
        return a_num > b_num
    # Same numeric trio; compare prerelease tags.
    a_pre, b_pre = a[3], b[3]
    if a_pre is None and b_pre is not None:
        return True  # release > prerelease
    if a_pre is not None and b_pre is None:
        return False
    if a_pre is None and b_pre is None:
        return False  # identical
    return (a_pre or "") > (b_pre or "")
