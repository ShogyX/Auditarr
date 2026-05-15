"""SQLAlchemy declarative base.

Naming convention is set explicitly so Alembic produces stable, deterministic
constraint names across PostgreSQL and SQLite.

Timestamp columns are tz-aware (`TIMESTAMP WITH TIME ZONE`) — required by
PostgreSQL when defaults produce ``datetime.now(UTC)`` values. SQLite stores
everything as text and silently accepts naive values, which masked this in
unit tests until the first PostgreSQL deploy.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


class Base(DeclarativeBase):
    """Project-wide declarative base."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        attrs = ", ".join(
            f"{c.name}={getattr(self, c.name)!r}"
            for c in self.__table__.columns
            if c.primary_key
        )
        return f"<{self.__class__.__name__} {attrs}>"


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns (tz-aware UTC)."""

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


def to_dict(obj: Any) -> dict[str, Any]:
    """Convert an ORM instance to a plain dict (no relationships)."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
