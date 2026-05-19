"""Version + commit comparison.

We intentionally do *not* depend on ``packaging`` or ``semver`` here.
Auditarr versions follow the strict ``MAJOR.MINOR.PATCH[-prerelease]``
shape that comes out of the image-build pipeline, plus the development
sentinel ``0.0.0-dev``. A small purpose-built comparator keeps the
behaviour explicit and avoids surprising prerelease ordering rules.

Version comparison rules:

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

Commit comparison rules (v1.9.x — commit-based feed):

* The ``"unknown"`` SHA is the commit-equivalent of ``0.0.0-dev``:
  it always counts as "older than any known commit". Source-tarball
  installs and dev environments without ``.git`` benefit from this.
* Different SHAs with a remote-newer date → newer.
* Same SHA → not newer, regardless of dates (handles clock skew).
* Different SHAs but the remote date is missing or older or equal →
  *not* newer. Operators on a branch ahead of ``main`` shouldn't see
  "update available" pointing at an older commit.
"""

from __future__ import annotations

import datetime as _dt
import re

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")
DEV_SENTINEL = "0.0.0-dev"
UNKNOWN_COMMIT = "unknown"


def parse(version: str) -> tuple[int, int, int, str | None] | None:
    """Parse a version string. Returns None when the shape doesn't match."""
    match = VERSION_RE.match(version.strip())
    if match is None:
        return None
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), pre)


def is_newer(candidate: str, installed: str) -> bool:
    """Return True when ``candidate`` should be considered an upgrade.

    This is the version-string comparator (release-tag feed). See
    :func:`is_newer_commit` for the commit-based equivalent.
    """
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


def is_newer_commit(
    candidate_sha: str,
    installed_sha: str,
    *,
    candidate_date: _dt.datetime | None = None,
    installed_date: _dt.datetime | None = None,
) -> bool:
    """Return True when ``candidate_sha`` should be considered newer.

    Decision tree (each rule short-circuits):

    1. Empty / unknown installed → always newer (``unknown`` is the
       commit-equivalent of the ``0.0.0-dev`` version sentinel).
    2. Identical SHAs → not newer.
    3. Both dates available → strictly later date wins. (Equal dates
       between different SHAs is the genuine ambiguity case — we
       refuse to claim "newer" rather than nag the operator about
       a side-branch commit.)
    4. Dates unavailable on one side → conservatively claim newer
       (matches the "different is upgrade" stance the version
       comparator uses for unknown shapes).
    """
    installed = (installed_sha or "").strip()
    candidate = (candidate_sha or "").strip()
    if not candidate:
        return False
    if not installed or installed == UNKNOWN_COMMIT:
        return True
    if installed == candidate:
        return False
    if candidate_date is not None and installed_date is not None:
        return candidate_date > installed_date
    # One side has no date — different SHAs with missing temporal
    # context → assume the operator wants to know.
    return True
