from __future__ import annotations

import io
import logging

import discord

from media_club.repositories import MediaRepository
from media_club.tmdb import TmdbClient, TmdbError, TmdbMovie

TMDB_ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."

logger = logging.getLogger(__name__)


def movie_embed(movie: TmdbMovie) -> discord.Embed:
    """Build a consistent Discord presentation for a TMDB movie."""
    embed = discord.Embed(
        title=movie.display_title,
        description=movie.overview or "No overview is available.",
        url=f"https://www.themoviedb.org/movie/{movie.id}",
        color=discord.Color.dark_teal(),
    )
    if movie.poster_url:
        embed.set_thumbnail(url=movie.poster_url)
    embed.set_footer(text=TMDB_ATTRIBUTION)
    return embed


def review_content(user: discord.abc.User, review: str, rating: str) -> str:
    """Format a member-attributed review for a bot-authored forum post."""
    lines = [f"**Review by {user.mention}**"]
    if rating.strip():
        lines.append(f"**Rating:** {rating.strip()}")
    lines.extend(("", review.strip(), "", f"_{TMDB_ATTRIBUTION}_"))
    return "\n".join(lines)


def movie_tag(forum: discord.ForumChannel, tag_name: str) -> discord.ForumTag | None:
    """Find a forum tag by its display name, ignoring case."""
    wanted = tag_name.casefold()
    return next((tag for tag in forum.available_tags if tag.name.casefold() == wanted), None)


def poster_file(movie: TmdbMovie, image: bytes) -> discord.File:
    """Wrap downloaded poster bytes as a Discord upload."""
    return discord.File(io.BytesIO(image), filename=f"tmdb-{movie.id}-poster.jpg")


def _options(movies: list[TmdbMovie]) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=movie.display_title[:100],
            value=str(movie.id),
            description=(movie.overview or "No overview available")[:100],
        )
        for movie in movies
    ]


class MovieSearchView(discord.ui.View):
    """Ephemeral picker for previewing and caching a movie."""

    def __init__(self, movies: list[TmdbMovie], repository: MediaRepository) -> None:
        super().__init__(timeout=180)
        self.add_item(MovieSearchSelect(movies, repository))


class MovieSearchSelect(discord.ui.Select[MovieSearchView]):
    """Select control that persists and displays the chosen movie."""

    def __init__(self, movies: list[TmdbMovie], repository: MediaRepository) -> None:
        self.movies = {str(movie.id): movie for movie in movies}
        self.repository = repository
        super().__init__(
            placeholder="Choose the correct movie",
            min_values=1,
            max_values=1,
            options=_options(movies),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Save the selected result and replace the picker with its details."""
        movie = self.movies[self.values[0]]
        item = await self.repository.save_tmdb_movie(movie)
        await interaction.response.edit_message(
            content=f"Saved **{movie.display_title}** as media item `{item.id}`.",
            embed=movie_embed(movie),
            view=None,
        )


class MoviePostView(discord.ui.View):
    """Picker that continues into the bot-authored forum-post workflow."""

    def __init__(
        self,
        movies: list[TmdbMovie],
        repository: MediaRepository,
        tmdb: TmdbClient,
        forum_channel_id: int,
        tag_name: str,
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(MoviePostSelect(movies, repository, tmdb, forum_channel_id, tag_name))


class MoviePostSelect(discord.ui.Select[MoviePostView]):
    """Open the review form after the member chooses a movie."""

    def __init__(
        self,
        movies: list[TmdbMovie],
        repository: MediaRepository,
        tmdb: TmdbClient,
        forum_channel_id: int,
        tag_name: str,
    ) -> None:
        self.movies = {str(movie.id): movie for movie in movies}
        self.repository = repository
        self.tmdb = tmdb
        self.forum_channel_id = forum_channel_id
        self.tag_name = tag_name
        super().__init__(
            placeholder="Choose a movie, then write your review",
            min_values=1,
            max_values=1,
            options=_options(movies),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show the review form for the chosen movie."""
        movie = self.movies[self.values[0]]
        modal = MovieReviewModal(
            movie,
            self.repository,
            self.tmdb,
            self.forum_channel_id,
            self.tag_name,
        )
        await interaction.response.send_modal(modal)
        if interaction.message is not None:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                logger.warning("Could not remove completed movie picker", exc_info=True)


class MovieReviewModal(discord.ui.Modal):
    """Collect a review before creating a complete forum post."""

    review: discord.ui.TextInput[MovieReviewModal] = discord.ui.TextInput(
        label="Your review",
        style=discord.TextStyle.paragraph,
        placeholder="What did you think?",
        min_length=1,
        max_length=1700,
    )
    rating: discord.ui.TextInput[MovieReviewModal] = discord.ui.TextInput(
        label="Rating (optional)",
        placeholder="Examples: 4/5, 8/10, or Great",
        required=False,
        max_length=30,
    )

    def __init__(
        self,
        movie: TmdbMovie,
        repository: MediaRepository,
        tmdb: TmdbClient,
        forum_channel_id: int,
        tag_name: str,
    ) -> None:
        super().__init__(title=f"Review {movie.title}"[:45], timeout=600)
        self.movie = movie
        self.repository = repository
        self.tmdb = tmdb
        self.forum_channel_id = forum_channel_id
        self.tag_name = tag_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the forum post and persist its selected media item."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = interaction.client.get_channel(self.forum_channel_id)
            if channel is None:
                channel = await interaction.client.fetch_channel(self.forum_channel_id)
            if not isinstance(channel, discord.ForumChannel):
                await interaction.followup.send(
                    "The configured movie destination is not a forum channel.",
                    ephemeral=True,
                )
                return

            tag = movie_tag(channel, self.tag_name)
            if tag is None:
                await interaction.followup.send(
                    f'The forum does not have a tag named "{self.tag_name}".',
                    ephemeral=True,
                )
                return

            image = await self.tmdb.download_poster(self.movie)
            content = review_content(interaction.user, str(self.review), str(self.rating))
            allowed_mentions = discord.AllowedMentions(users=False, roles=False, everyone=False)
            if image is None:
                created = await channel.create_thread(
                    name=self.movie.display_title,
                    content=content,
                    applied_tags=[tag],
                    allowed_mentions=allowed_mentions,
                )
            else:
                created = await channel.create_thread(
                    name=self.movie.display_title,
                    content=content,
                    file=poster_file(self.movie, image),
                    applied_tags=[tag],
                    allowed_mentions=allowed_mentions,
                )
            await self.repository.save_tmdb_movie(self.movie)
            await interaction.followup.send(
                f"Created {created.thread.mention} for **{self.movie.display_title}**.",
                ephemeral=True,
            )
        except (discord.HTTPException, TmdbError):
            logger.exception("Could not create movie forum post")
            await interaction.followup.send(
                "I couldn't create that forum post. Check my forum permissions and try again.",
                ephemeral=True,
            )


class MovieAttachView(discord.ui.View):
    """Picker that enriches an existing member-authored forum post."""

    def __init__(
        self,
        movies: list[TmdbMovie],
        repository: MediaRepository,
        tmdb: TmdbClient,
        thread: discord.Thread,
        tag_name: str,
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(MovieAttachSelect(movies, repository, tmdb, thread, tag_name))


class MovieAttachSelect(discord.ui.Select[MovieAttachView]):
    """Apply selected metadata and artwork to an existing forum post."""

    def __init__(
        self,
        movies: list[TmdbMovie],
        repository: MediaRepository,
        tmdb: TmdbClient,
        thread: discord.Thread,
        tag_name: str,
    ) -> None:
        self.movies = {str(movie.id): movie for movie in movies}
        self.repository = repository
        self.tmdb = tmdb
        self.thread = thread
        self.tag_name = tag_name
        super().__init__(
            placeholder="Choose the movie to attach to this post",
            min_values=1,
            max_values=1,
            options=_options(movies),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Rename, tag, and add poster artwork to the current forum thread."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        movie = self.movies[self.values[0]]
        parent = self.thread.parent
        if not isinstance(parent, discord.ForumChannel):
            await interaction.edit_original_response(
                content="This command only works inside a forum post.", view=None
            )
            return

        tag = movie_tag(parent, self.tag_name)
        if tag is None:
            await interaction.edit_original_response(
                content=f'This forum does not have a tag named "{self.tag_name}".',
                view=None,
            )
            return

        try:
            applied_tags = list(self.thread.applied_tags)
            if all(applied.id != tag.id for applied in applied_tags):
                applied_tags.append(tag)
            await self.thread.edit(name=movie.display_title, applied_tags=applied_tags)
            image = await self.tmdb.download_poster(movie)
            if image is not None:
                await self.thread.send(
                    f"Poster for **{movie.display_title}**\n_{TMDB_ATTRIBUTION}_",
                    file=poster_file(movie, image),
                )
            else:
                await self.thread.send(
                    f"Linked this post to **{movie.display_title}**. No poster is available.\n"
                    f"_{TMDB_ATTRIBUTION}_"
                )
            await self.repository.save_tmdb_movie(movie)
            await interaction.edit_original_response(
                content=f"Updated {self.thread.mention} with **{movie.display_title}**.",
                view=None,
            )
        except (discord.HTTPException, TmdbError):
            logger.exception("Could not enrich movie forum post")
            await interaction.edit_original_response(
                content="I couldn't update this post. Check my forum permissions and try again.",
                view=None,
            )
