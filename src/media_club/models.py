from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from media_club.media_types import MediaKind


class Base(DeclarativeBase):
    """Base class for persisted application models."""


class User(Base):
    """A Discord user known to Ghoul Bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class MediaItem(Base):
    """Normalized media metadata imported from an external provider."""

    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_media_items_provider_id"),
        CheckConstraint(
            "media_type IN ('movie', 'tv_show', 'book', 'comic', 'podcast', "
            "'music', 'video_game', 'other')",
            name="ck_media_items_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    media_type: Mapped[MediaKind] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    release_date: Mapped[date | None] = mapped_column(Date())
    overview: Mapped[str | None] = mapped_column(Text())
    poster_path: Mapped[str | None] = mapped_column(String(255))
    artwork_data: Mapped[bytes | None] = mapped_column(LargeBinary())
    artwork_content_type: Mapped[str | None] = mapped_column(String(100))
    artwork_filename: Mapped[str | None] = mapped_column(String(255))
    provider_payload: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MediaLog(Base):
    """A member's optional review and activity record for a media item."""

    __tablename__ = "media_logs"
    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR rating IN (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)",
            name="ck_media_logs_rating_range",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    media_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("media_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    review: Mapped[str | None] = mapped_column(Text())
    rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))
    activity_date: Mapped[date | None] = mapped_column(Date())
    forum_thread_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    forum_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    media_item: Mapped[MediaItem] = relationship(lazy="joined")
