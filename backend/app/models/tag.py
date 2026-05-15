"""Media tag model.

Many-to-many between :class:`MediaFile` and free-form tag strings. Tags can
come from rule actions or from integration syncs (Sonarr/Radarr tag mirror).
The source string lets us distinguish without separate tables.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class MediaTag(Base, TimestampMixin):
    __tablename__ = "media_tags"
    __table_args__ = (
        UniqueConstraint(
            "media_file_id", "name", "source", name="uq_media_tags_file_name_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    media_file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", index=True
    )
