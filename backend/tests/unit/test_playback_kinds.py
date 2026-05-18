"""``PLAYBACK_KINDS`` whitelist — controls which integration kinds
the playback poller will iterate over.

Tracearr's provider implements ``fetch_playback_events`` (see
``backend/plugins/tracearr/backend.py``) but the worker dropped it
on the floor before the poller ever saw it, so its data never
made it into ``playback_events``. The fix is a single-set
extension; this test pins the membership so a future refactor
doesn't silently regress.
"""

from __future__ import annotations

from app.worker import PLAYBACK_KINDS


def test_playback_kinds_contains_expected() -> None:
    assert {"plex", "jellyfin", "tracearr"} <= PLAYBACK_KINDS


def test_playback_kinds_excludes_pure_arrs() -> None:
    # Sonarr/Radarr/Bazarr don't implement ``fetch_playback_events``.
    # Including them would either error or generate zero-row noise.
    for kind in ("sonarr", "radarr", "bazarr"):
        assert kind not in PLAYBACK_KINDS
