from sqlalchemy import select

from media_club.database import Database
from media_club.models import MediaItem
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
