"""Job catalogue singleton.

The catalogue is the single source of truth for what kinds of background
work exist. Schedules reference catalogue entries by ``key``; the
scheduler dispatches through the catalogue; the frontend renders
schedule forms by reading the catalogue.

The dataclasses themselves (:class:`JobSpec`, :class:`JobCatalogue`,
``JobRunner``) live in :mod:`app.automation.types` so the runners in
:mod:`app.automation.jobs` can reference them without creating an
import cycle (this module imports ``jobs`` lazily to populate the
singleton — see :func:`get_catalogue`).
"""

from __future__ import annotations

from app.automation.types import JobCatalogue, JobRunner, JobSpec

_catalogue: JobCatalogue | None = None


def get_catalogue() -> JobCatalogue:
    global _catalogue
    if _catalogue is None:
        # Lazy import so the static import graph stays acyclic —
        # see ``app.automation.types`` for the why.
        from app.automation.jobs import register_builtin_jobs

        _catalogue = JobCatalogue()
        register_builtin_jobs(_catalogue)
    return _catalogue


def reset_catalogue() -> None:
    """Test helper — wipe the registered jobs."""
    global _catalogue
    _catalogue = None


__all__ = [
    "JobCatalogue",
    "JobRunner",
    "JobSpec",
    "get_catalogue",
    "reset_catalogue",
]
