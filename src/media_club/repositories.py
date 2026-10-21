from datetime import date
from decimal import Decimal
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from media_club.database import Database
from media_club.media_types import MediaKind
from media_club.models import MediaItem, MediaLog
from media_club.tmdb import TmdbMovie


class MediaRepository:
    """Persist normalized media items independent of the calling interface."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def save_tmdb_movie(self, movie: TmdbMovie) -> MediaItem:
        """Insert a TMDB movie or refresh its cached metadata."""
        async with self.database.sessions() as session:
            item = await session.scalar(
                select(MediaItem).where(
                    MediaItem.provider == "tmdb",
                    MediaItem.provider_id == str(movie.id),
                )
            )
            if item is None:
                item = MediaItem(
                    media_type="movie",
                    provider="tmdb",
                    provider_id=str(movie.id),
                    title=movie.title,
                    release_date=movie.release_date,
                    overview=movie.overview or None,
                    poster_path=movie.poster_path,
                    provider_payload=movie.payload,
                )
                session.add(item)
            else:
                item.title = movie.title
                item.release_date = movie.release_date
                item.overview = movie.overview or None
                item.poster_path = movie.poster_path
                item.provider_payload = movie.payload

            await session.commit()
            await session.refresh(item)
            return item

    async def save_manual_item(
        self,
        media_type: MediaKind,
        title: str,
        *,
        artwork_data: bytes | None = None,
        artwork_content_type: str | None = None,
        artwork_filename: str | None = None,
    ) -> MediaItem:
        """Create an independently editable manual media item."""
        async with self.database.sessions() as session:
            item = MediaItem(
                media_type=media_type,
                provider="manual",
                provider_id=str(uuid4()),
                title=title,
                release_date=None,
                overview=None,
                poster_path=None,
                artwork_data=artwork_data,
                artwork_content_type=artwork_content_type,
                artwork_filename=artwork_filename,
                provider_payload={},
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def create_log(
        self,
        media_item_id: int,
        discord_user_id: int,
        *,
        review: str | None,
        rating: Decimal | None,
        activity_date: date | None,
        season_year: int,
        poster_path: str | None = None,
    ) -> MediaLog:
        """Create a member-owned log record without assuming a forum post."""
        async with self.database.sessions() as session:
            media_log = MediaLog(
                media_item_id=media_item_id,
                discord_user_id=discord_user_id,
                review=review,
                rating=rating,
                activity_date=activity_date,
                season_year=season_year,
                poster_path=poster_path,
            )
            session.add(media_log)
            await session.commit()
            return await self._load_log(session, media_log.id)

    async def set_forum_post(
        self,
        log_id: int,
        *,
        thread_id: int,
        message_id: int,
    ) -> MediaLog:
        """Associate a successfully created Discord forum post with a log."""
        async with self.database.sessions() as session:
            media_log = await self._load_log(session, log_id)
            media_log.forum_thread_id = thread_id
            media_log.forum_message_id = message_id
            await session.commit()
            return await self._load_log(session, log_id)

    async def get_log_for_thread(self, thread_id: int) -> MediaLog | None:
        """Find a log by its Discord forum thread."""
        async with self.database.sessions() as session:
            return cast(
                MediaLog | None,
                await session.scalar(
                    select(MediaLog)
                    .options(joinedload(MediaLog.media_item))
                    .where(MediaLog.forum_thread_id == thread_id)
                ),
            )

    async def get_owned_log(self, log_id: int, discord_user_id: int) -> MediaLog | None:
        """Find a log only when it belongs to the requesting member."""
        async with self.database.sessions() as session:
            return cast(
                MediaLog | None,
                await session.scalar(
                    select(MediaLog)
                    .options(joinedload(MediaLog.media_item))
                    .where(
                        MediaLog.id == log_id,
                        MediaLog.discord_user_id == discord_user_id,
                    )
                ),
            )

    async def list_owned_logs(
        self,
        discord_user_id: int,
        *,
        offset: int = 0,
        limit: int = 25,
    ) -> list[MediaLog]:
        """Return a newest-first page of all logs owned by a member."""
        async with self.database.sessions() as session:
            result = await session.scalars(
                select(MediaLog)
                .options(joinedload(MediaLog.media_item))
                .where(MediaLog.discord_user_id == discord_user_id)
                .order_by(MediaLog.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.unique())

    async def update_log_data(
        self,
        log_id: int,
        *,
        review: str | None,
        rating: Decimal | None,
        activity_date: date | None,
    ) -> MediaLog:
        """Update only the member-specific portion of a log."""
        async with self.database.sessions() as session:
            media_log = await self._load_log(session, log_id)
            media_log.review = review
            media_log.rating = rating
            media_log.activity_date = activity_date
            await session.commit()
            return await self._load_log(session, log_id)

    async def replace_log_media(
        self,
        log_id: int,
        media_item_id: int,
    ) -> MediaLog:
        """Point a log at replacement media while preserving its forum-post identity."""
        async with self.database.sessions() as session:
            media_log = await self._load_log(session, log_id)
            media_log.media_item_id = media_item_id
            media_log.poster_path = None
            await session.commit()
            return await self._load_log(session, log_id)

    async def delete_log(self, log_id: int) -> None:
        """Permanently remove a log from activity history."""
        async with self.database.sessions() as session:
            media_log = await session.get(MediaLog, log_id)
            if media_log is not None:
                await session.delete(media_log)
                await session.commit()

    async def _load_log(self, session: AsyncSession, log_id: int) -> MediaLog:
        media_log = await session.scalar(
            select(MediaLog).options(joinedload(MediaLog.media_item)).where(MediaLog.id == log_id)
        )
        if media_log is None:
            raise LookupError(f"Media log {log_id} does not exist")
        return media_log
