import logging

import discord
from discord import app_commands

from media_club.config import Settings
from media_club.database import Database
from media_club.movie_ui import MovieSearchView
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
            await interaction.response.send_message(
                f"Environment: **{config.app_env}**\n"
                f"Database: **{database_status}**\n"
                f"TMDB: **{tmdb_status}**",
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

        self.tree.add_command(movie_group, guild=self.guild)

    async def setup_hook(self) -> None:
        """Register guild commands immediately in the staging server."""
        synced = await self.tree.sync(guild=self.guild)
        logger.info("Synced %d command(s) to guild %s", len(synced), self.guild.id)

    async def on_ready(self) -> None:
        """Log successful Gateway readiness without exposing credentials."""
        logger.info("Discord connected as %s", self.user)
