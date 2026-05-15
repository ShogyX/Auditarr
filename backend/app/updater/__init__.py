"""Updater subsystem (Stage 11).

Polls a release feed for newer versions, surfaces them in the UI,
and bridges operator-triggered applies to a host-side helper script
through a sentinel file pair.
"""

from app.updater.feed import FeedResult, fetch_feed
from app.updater.service import UpdaterService, UpdaterStatus
from app.updater.versioning import DEV_SENTINEL, is_newer, parse

__all__ = [
    "DEV_SENTINEL",
    "FeedResult",
    "UpdaterService",
    "UpdaterStatus",
    "fetch_feed",
    "is_newer",
    "parse",
]
