"""Catalogue type definitions.

Carved out of :mod:`app.automation.catalogue` so the job runners in
:mod:`app.automation.jobs` can reference ``JobSpec`` and
``JobCatalogue`` without creating an import cycle:

* ``catalogue.py`` only reaches into ``jobs.py`` lazily, inside
  ``get_catalogue()`` (to populate the singleton).
* ``jobs.py`` needs the dataclass shape at module import time
  to build ``JobSpec(...)`` instances.

With both files importing from this neutral module instead of each
other, the static import graph is acyclic.
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


__all__ = ["JobRunner", "JobSpec", "JobCatalogue"]
