"""Job catalogue.

The catalogue is the single source of truth for what kinds of background
work exist. Schedules reference catalogue entries by ``key``; the
scheduler dispatches through the catalogue; the frontend renders
schedule forms by reading the catalogue.

Each :class:`JobSpec` carries:

* ``key``     — stable identifier used in the DB
* ``label``   — UI display name
* ``args_schema`` — minimal description of bound arguments (mostly for
  the frontend; the runner only checks required keys are present)
* ``timeout_seconds`` — default per-invocation budget
* ``runner``  — async callable taking ``(session, args, ctx)`` and
  returning a JSON-serializable result

The runners themselves live in :mod:`app.automation.jobs`. Keeping the
catalogue separate from the runners keeps import order sane: the
scheduler, API, and frontend can all read the catalogue without pulling
in the heavy services those runners ultimately touch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

JobRunner = Callable[
    [AsyncSession, dict[str, Any], dict[str, Any]],  # (session, args, ctx)
    Awaitable[dict[str, Any]],
]


@dataclass(slots=True)
class JobSpec:
    key: str
    label: str
    description: str
    args_schema: dict[str, Any]
    timeout_seconds: int
    runner: JobRunner
    required_args: tuple[str, ...] = field(default_factory=tuple)


class JobCatalogue:
    """Process-wide registry. Populated at import time."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobSpec] = {}

    def register(self, spec: JobSpec) -> None:
        if spec.key in self._jobs:
            raise ValueError(f"Job already registered: {spec.key!r}")
        self._jobs[spec.key] = spec

    def get(self, key: str) -> JobSpec | None:
        return self._jobs.get(key)

    def require(self, key: str) -> JobSpec:
        spec = self.get(key)
        if spec is None:
            raise KeyError(f"Unknown job kind: {key!r}")
        return spec

    def list_all(self) -> list[JobSpec]:
        return sorted(self._jobs.values(), key=lambda j: j.label)

    def validate_args(self, key: str, args: dict[str, Any]) -> None:
        spec = self.require(key)
        missing = [k for k in spec.required_args if k not in args]
        if missing:
            raise ValueError(
                f"Job {key!r} requires arguments: {missing}"
            )


_catalogue: JobCatalogue | None = None


def get_catalogue() -> JobCatalogue:
    global _catalogue
    if _catalogue is None:
        from app.automation.jobs import register_builtin_jobs

        _catalogue = JobCatalogue()
        register_builtin_jobs(_catalogue)
    return _catalogue


def reset_catalogue() -> None:
    """Test helper — wipe the registered jobs."""
    global _catalogue
    _catalogue = None
