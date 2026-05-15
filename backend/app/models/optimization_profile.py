"""Optimization profile.

A named transcoding preset. Each profile encodes everything the worker
needs to invoke ffmpeg: video codec/quality, container, audio handling,
optional scaling. Rule ``queue_optimization`` actions reference profiles
by name; the worker matches the profile to its ffmpeg argv when it
dequeues an item.

Profiles are JSON-backed (the ``settings`` column) rather than carrying
one column per knob — this lets the profile vocabulary grow without
schema churn. The semantics of the keys live in
:mod:`app.optimization.profile_schema`, which validates documents on
save and on load.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class OptimizationProfile(Base, TimestampMixin):
    __tablename__ = "optimization_profiles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Validated against app.optimization.profile_schema.
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # When set, the worker rejects inputs above this size to keep small
    # boxes from chewing on 80 GB remuxes for a day.
    max_input_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stage 7 (audit follow-up): optional integration that owns
    # execution of this profile. When NULL (default + every existing
    # row), the in-process ffmpeg runner takes the job. When set, the
    # worker dispatches to the named integration (e.g. Tdarr, future
    # Unmanic plugin). The runtime wiring lands when the first such
    # plugin is added — Stage 7 ships only the column + the form
    # selector so profiles created today can be edited to specify a
    # routing target without a schema migration later.
    #
    # Stored as a free-form String rather than a FK so the column
    # doesn't tie the optimization layer's lifecycle to the
    # integration table (deleting an integration shouldn't cascade-
    # null out routing on every profile). The worker validates the
    # id at run time and falls back to in-process if it can't resolve.
    optimization_integration_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
