import logging

import discord
from discord import app_commands

from media_club.config import Settings
from media_club.database import Database
from media_club.movie_ui import MovieAttachView, MoviePostView, MovieSearchView
from media_club.repositories import MediaRepository
from media_club.tmdb import TmdbClient, TmdbError

logger = logging.getLogger(__name__)


class GhoulBot(discord.Client):
    """Discord client and application-command registry."""

    def __init__(
        self,
        config: Settings,
        database: Database,
        tmdb: TmdbClient | None,
    ) -> None:
        application_id = config.discord_application_id
        if application_id is None:
            raise ValueError("DISCORD_APPLICATION_ID is required")
        guild_id = config.discord_guild_id
        if guild_id is None:
            raise ValueError("DISCORD_GUILD_ID is required")

        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            intents=intents,
            application_id=application_id,
        )
        self.database = database
        self.tmdb = tmdb
        self.media_repository = MediaRepository(database)
        self.guild = discord.Object(id=guild_id)
        self.tree = app_commands.CommandTree(self)
        self._register_commands(config)

    def _register_commands(self, config: Settings) -> None:
        @self.tree.command(name="ping", description="Check whether Ghoul Bot is online")
        @app_commands.guilds(self.guild)
        async def ping(interaction: discord.Interaction) -> None:
            latency_ms = round(self.latency * 1000)
            await interaction.response.send_message(
                f"Pong! Ghoul Bot is online ({latency_ms} ms).",
                ephemeral=True,
            )

        @self.tree.command(name="status", description="Check Ghoul Bot's dependencies")
        @app_commands.guilds(self.guild)
        async def status(interaction: discord.Interaction) -> None:
            database_status = "connected" if await self.database.ping() else "unavailable"
            tmdb_status = "configured" if self.tmdb is not None else "not configured"
            movie_forum_status = (
                "configured"
                if config.discord_movie_forum_channel_id is not None
                else "not configured"
            )
            await interaction.response.send_message(
                f"Environment: **{config.app_env}**\n"
                f"Database: **{database_status}**\n"
                f"TMDB: **{tmdb_status}**\n"
                f"Movie forum: **{movie_forum_status}**",
                ephemeral=True,
            )

        movie_group = app_commands.Group(name="movie", description="Find and manage movies")

        @movie_group.command(name="search", description="Search TMDB for a movie")
        @app_commands.describe(title="Movie title to search for")
        async def movie_search(interaction: discord.Interaction, title: str) -> None:
            if self.tmdb is None:
                await interaction.response.send_message(
                    "TMDB is not configured yet.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                movies = await self.tmdb.search_movies(title)
            except TmdbError:
                logger.exception("TMDB search failed")
                await interaction.followup.send(
                    "TMDB could not complete that search. Please try again shortly.",
                    ephemeral=True,
                )
                return

            if not movies:
                await interaction.followup.send(
                    f'No movies matched "{title}".',
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                f"Search results for **{title}**:",
                view=MovieSearchView(movies, self.media_repository),
                ephemeral=True,
            )

        @movie_group.command(name="post", description="Create a movie review forum post")
        @app_commands.describe(title="Movie title to search for")
        async def movie_post(interaction: discord.Interaction, title: str) -> None:
            forum_channel_id = config.discord_movie_forum_channel_id
            if self.tmdb is None:
                await interaction.response.send_message(
                    "TMDB is not configured yet.", ephemeral=True
                )
                return
            if forum_channel_id is None:
                await interaction.response.send_message(
                    "The movie forum is not configured yet.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                movies = await self.tmdb.search_movies(title)
            except TmdbError:
                logger.exception("TMDB search failed")
                await interaction.followup.send(
                    "TMDB could not complete that search. Please try again shortly.",
                    ephemeral=True,
                )
                return
            if not movies:
                await interaction.followup.send(f'No movies matched "{title}".', ephemeral=True)
                return

            await interaction.followup.send(
                "Choose a movie. You'll write your review on the next screen:",
                view=MoviePostView(
                    movies,
                    self.media_repository,
                    self.tmdb,
                    forum_channel_id,
                    config.discord_movie_tag_name,
                ),
                ephemeral=True,
            )

        @movie_group.command(name="attach", description="Add movie details to this forum post")
        @app_commands.describe(title="Movie title to search for")
        async def movie_attach(interaction: discord.Interaction, title: str) -> None:
            if self.tmdb is None:
                await interaction.response.send_message(
                    "TMDB is not configured yet.", ephemeral=True
                )
                return

            channel = interaction.channel
            if not isinstance(channel, discord.Thread) or not isinstance(
                channel.parent, discord.ForumChannel
            ):
                await interaction.response.send_message(
                    "Use this command inside an existing forum post.", ephemeral=True
                )
                return

            is_owner = channel.owner_id == interaction.user.id
            is_moderator = isinstance(interaction.user, discord.Member) and (
                interaction.user.guild_permissions.manage_threads
            )
            if not is_owner and not is_moderator:
                await interaction.response.send_message(
                    "Only the post's creator or a thread moderator can attach a movie.",
                    ephemeral=True,
                )
                return

            configured_forum_id = config.discord_movie_forum_channel_id
            if configured_forum_id is not None and channel.parent.id != configured_forum_id:
                await interaction.response.send_message(
                    "This is not the configured movie forum.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                movies = await self.tmdb.search_movies(title)
            except TmdbError:
                logger.exception("TMDB search failed")
                await interaction.followup.send(
                    "TMDB could not complete that search. Please try again shortly.",
                    ephemeral=True,
                )
                return
            if not movies:
                await interaction.followup.send(f'No movies matched "{title}".', ephemeral=True)
                return

            await interaction.followup.send(
                "Choose the movie that belongs to this forum post:",
                view=MovieAttachView(
                    movies,
                    self.media_repository,
                    self.tmdb,
                    channel,
                    config.discord_movie_tag_name,
                ),
                ephemeral=True,
            )

        self.tree.add_command(movie_group, guild=self.guild)

    async def setup_hook(self) -> None:
        """Register guild commands immediately in the staging server."""
        synced = await self.tree.sync(guild=self.guild)
        logger.info("Synced %d command(s) to guild %s", len(synced), self.guild.id)

    async def on_ready(self) -> None:
        """Log successful Gateway readiness without exposing credentials."""
        logger.info("Discord connected as %s", self.user)
