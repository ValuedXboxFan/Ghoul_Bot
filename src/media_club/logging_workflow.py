from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import discord

from media_club.media_types import MEDIA_TYPES, MediaKind
from media_club.models import MediaItem, MediaLog
from media_club.tmdb import TmdbClient, TmdbMovie

MAX_ARTWORK_BYTES = 5 * 1024 * 1024


@dataclass(slots=True)
class MediaDraft:
    """Temporary selected or manually entered media details."""

    kind: MediaKind
    title: str
    tmdb_movie: TmdbMovie | None = None
    artwork_data: bytes | None = None
    artwork_content_type: str | None = None
    artwork_filename: str | None = None


@dataclass(frozen=True, slots=True)
class LogData:
    """Validated member-specific logging fields."""

    review: str | None
    rating: Decimal | None
    activity_date: date | None


@dataclass(frozen=True, slots=True)
class Artwork:
    """Artwork bytes ready for a Discord message upload."""

    data: bytes
    filename: str


class ForumConfigurationError(RuntimeError):
    """Raised when the configured forum or its required tag is unavailable."""


def display_title(item: MediaItem) -> str:
    """Return the normalized thread title for a persisted media item."""
    if item.media_type == "movie" and item.release_date is not None:
        return f"{item.title} ({item.release_date.year})"[:100]
    return item.title[:100]


def log_embed(user: discord.User | discord.Member, media_log: MediaLog) -> discord.Embed:
    """Render member log data with an avatar snapshot and durable log identifier."""
    item = media_log.media_item
    embed = discord.Embed(color=discord.Color.dark_teal())
    embed.set_author(
        name=f"{user.display_name}'s log",
        icon_url=user.display_avatar.url,
    )
    if media_log.activity_date is not None:
        embed.add_field(
            name="Date",
            value=(
                f"{media_log.activity_date.strftime('%B')} "
                f"{media_log.activity_date.day}, {media_log.activity_date.year}"
            ),
            inline=False,
        )
    if media_log.rating is not None:
        embed.add_field(
            name="Rating",
            value=rating_stars(media_log.rating),
            inline=False,
        )
    if media_log.review:
        embed.add_field(name="Review", value=media_log.review, inline=False)

    footer = f"{MEDIA_TYPES[item.media_type].label} log #{media_log.id}"
    if item.provider == "tmdb":
        footer = f"{footer} • Data & artwork from TMDB"
    embed.set_footer(text=footer)
    return embed


def rating_stars(rating: Decimal) -> str:
    """Render a strict half-step rating as a compact five-star display."""
    doubled = int(rating * 2)
    full_stars, has_half = divmod(doubled, 2)
    empty_stars = 5 - full_stars - has_half
    return f"{'⭐' * full_stars}{'½' if has_half else ''}{'☆' * empty_stars} ({rating.normalize()})"


def log_content(user: discord.User | discord.Member, media_log: MediaLog) -> str:
    """Render the live user reference above the artwork and structured log data."""
    return f"**Logged by {user.mention}**"


class ForumPublisher:
    """Create and update Discord forum representations of media logs."""

    def __init__(self, forum_channel_id: int | None, tmdb: TmdbClient | None) -> None:
        self.forum_channel_id = forum_channel_id
        self.tmdb = tmdb

    async def publish(
        self,
        client: discord.Client,
        user: discord.User | discord.Member,
        media_log: MediaLog,
        item: MediaItem,
    ) -> tuple[discord.Thread, discord.Message]:
        """Create a tagged forum post with optional artwork."""
        forum = await self._forum(client)
        tag_name = MEDIA_TYPES[item.media_type].tag_name
        tag = next(
            (tag for tag in forum.available_tags if tag.name.casefold() == tag_name.casefold()),
            None,
        )
        if tag is None:
            raise ForumConfigurationError(f'The configured forum does not have a "{tag_name}" tag.')

        media_log.media_item = item
        artwork = await self.artwork(item)
        if artwork is None:
            created = await forum.create_thread(
                name=display_title(item),
                content=log_content(user, media_log),
                embed=log_embed(user, media_log),
                applied_tags=[tag],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            created = await forum.create_thread(
                name=display_title(item),
                content=log_content(user, media_log),
                embed=log_embed(user, media_log),
                applied_tags=[tag],
                allowed_mentions=discord.AllowedMentions.none(),
                file=discord.File(io.BytesIO(artwork.data), filename=artwork.filename),
            )
        return created.thread, created.message

    async def update_log_message(
        self,
        client: discord.Client,
        user: discord.User | discord.Member,
        media_log: MediaLog,
    ) -> None:
        """Update review/rating/date while retaining existing artwork."""
        if media_log.forum_thread_id is None or media_log.forum_message_id is None:
            return
        channel = client.get_channel(media_log.forum_thread_id)
        if channel is None:
            channel = await client.fetch_channel(media_log.forum_thread_id)
        if not isinstance(channel, discord.Thread):
            raise ForumConfigurationError("The stored forum thread no longer exists.")
        message = await channel.fetch_message(media_log.forum_message_id)
        await message.edit(
            content=log_content(user, media_log),
            embed=log_embed(user, media_log),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def artwork(self, item: MediaItem) -> Artwork | None:
        """Resolve stored manual artwork or download provider artwork."""
        if item.artwork_data is not None:
            filename = Path(item.artwork_filename or "artwork.jpg").name
            return Artwork(item.artwork_data, filename)
        if item.provider == "tmdb" and self.tmdb is not None:
            data = await self.tmdb.download_poster_path(item.poster_path)
            if data is not None:
                return Artwork(data, f"tmdb-{item.provider_id}-poster.jpg")
        return None

    async def delete_thread(self, client: discord.Client, thread_id: int) -> None:
        """Delete a forum thread if it still exists."""
        channel = client.get_channel(thread_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(thread_id)
            except discord.NotFound:
                return
        if isinstance(channel, discord.Thread):
            await channel.delete(reason="Owner deleted Ghoul Bot media log")

    async def _forum(self, client: discord.Client) -> discord.ForumChannel:
        if self.forum_channel_id is None:
            raise ForumConfigurationError("The media forum is not configured.")
        channel = client.get_channel(self.forum_channel_id)
        if channel is None:
            channel = await client.fetch_channel(self.forum_channel_id)
        if not isinstance(channel, discord.ForumChannel):
            raise ForumConfigurationError("The configured destination is not a forum channel.")
        return channel
