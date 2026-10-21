import logging
from typing import cast

import discord
from discord import app_commands

from media_club.config import Settings
from media_club.database import Database
from media_club.log_ui import (
    LOGS_PAGE_SIZE,
    DeleteConfirmationView,
    EditChoiceView,
    LogDependencies,
    LogsView,
    ManualMediaModal,
    MovieLogStartView,
)
from media_club.logging_workflow import ForumPublisher, MediaDraft
from media_club.media_types import MEDIA_TYPES, MediaKind
from media_club.models import MediaLog
from media_club.repositories import MediaRepository
from media_club.season import (
    active_season,
    closed_season_message,
    season_is_editable,
)
from media_club.tmdb import TmdbClient

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
        self.publisher = ForumPublisher(config.media_forum_channel_id, tmdb)
        self.log_dependencies = LogDependencies(
            repository=self.media_repository,
            publisher=self.publisher,
            tmdb=tmdb,
        )
        self.guild = discord.Object(id=guild_id)
        self.tree = app_commands.CommandTree(self)
        self._register_commands(config)

    def _register_commands(self, config: Settings) -> None:
        @self.tree.command(name="ping", description="Check whether Ghoul Bot is online")
        @app_commands.guilds(self.guild)
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.checks.has_permissions(manage_guild=True)
        async def ping(interaction: discord.Interaction) -> None:
            latency_ms = round(self.latency * 1000)
            await interaction.response.send_message(
                f"Pong! Ghoul Bot is online ({latency_ms} ms).",
                ephemeral=True,
            )

        @self.tree.command(name="status", description="Check Ghoul Bot's dependencies")
        @app_commands.guilds(self.guild)
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.checks.has_permissions(manage_guild=True)
        async def status(interaction: discord.Interaction) -> None:
            database_status = "connected" if await self.database.ping() else "unavailable"
            tmdb_status = "configured" if self.tmdb is not None else "not configured"
            media_forum_status = (
                "configured" if config.media_forum_channel_id is not None else "not configured"
            )
            await interaction.response.send_message(
                f"Environment: **{config.app_env}**\n"
                f"Database: **{database_status}**\n"
                f"TMDB: **{tmdb_status}**\n"
                f"Media forum: **{media_forum_status}**",
                ephemeral=True,
            )

        async def operations_permission_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            if isinstance(error, app_commands.MissingPermissions):
                await interaction.response.send_message(
                    "You need the Manage Server permission to use that command.",
                    ephemeral=True,
                )
                return
            raise error

        ping.error(operations_permission_error)
        status.error(operations_permission_error)

        async def start_log_workflow(
            interaction: discord.Interaction,
            media_type: app_commands.Choice[str],
            title: app_commands.Range[str, 1, 100],
            *,
            publish: bool,
        ) -> None:
            if active_season() is None:
                await interaction.response.send_message(
                    "Logging is closed. The next season opens September 28 and runs through "
                    "November 7.",
                    ephemeral=True,
                )
                return
            clean_title = title.strip()
            media_kind = cast(MediaKind, media_type.value)
            if media_kind == "movie":
                await interaction.response.send_message(
                    "How would you like to add this movie?",
                    view=MovieLogStartView(
                        interaction.user.id,
                        clean_title,
                        self.log_dependencies,
                        publish=publish,
                    ),
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                ManualMediaModal(
                    MediaDraft(kind=media_kind, title=clean_title),
                    self.log_dependencies,
                    publish=publish,
                )
            )

        media_type_choices = [
            app_commands.Choice(name=definition.tag_name, value=key)
            for key, definition in MEDIA_TYPES.items()
        ]

        @self.tree.command(name="log", description="Add media to your private ledger")
        @app_commands.guilds(self.guild)
        @app_commands.describe(
            media_type="What kind of media are you logging?",
            title="Title or search text",
        )
        @app_commands.choices(media_type=media_type_choices)
        async def log(
            interaction: discord.Interaction,
            media_type: app_commands.Choice[str],
            title: app_commands.Range[str, 1, 100],
        ) -> None:
            await start_log_workflow(interaction, media_type, title, publish=False)

        @self.tree.command(name="post", description="Log media and create a forum post")
        @app_commands.guilds(self.guild)
        @app_commands.describe(
            media_type="What kind of media are you posting?",
            title="Title or search text",
        )
        @app_commands.choices(media_type=media_type_choices)
        async def post(
            interaction: discord.Interaction,
            media_type: app_commands.Choice[str],
            title: app_commands.Range[str, 1, 100],
        ) -> None:
            await start_log_workflow(interaction, media_type, title, publish=True)

        @self.tree.command(name="about", description="View Ghoul Bot credits and data sources")
        @app_commands.guilds(self.guild)
        async def about(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="About Ghoul Bot",
                description=(
                    "Ghoul Bot helps this server log and discuss movies and other media.\n\n"
                    "Movie data and artwork are supplied by "
                    "[The Movie Database (TMDB)](https://www.themoviedb.org/).\n\n"
                    "This product uses the TMDB API but is not endorsed or certified by TMDB."
                ),
                color=discord.Color.dark_teal(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        async def owned_thread_log(interaction: discord.Interaction) -> MediaLog | None:
            channel = interaction.channel
            if not isinstance(channel, discord.Thread) or not isinstance(
                channel.parent, discord.ForumChannel
            ):
                await interaction.response.send_message(
                    "Use this command inside a Ghoul Bot forum post.", ephemeral=True
                )
                return None
            media_log = await self.media_repository.get_log_for_thread(channel.id)
            if media_log is None:
                await interaction.response.send_message(
                    "This forum post is not registered as a Ghoul Bot log.", ephemeral=True
                )
                return None
            if media_log.discord_user_id != interaction.user.id:
                await interaction.response.send_message(
                    "Only the registered owner can manage this log.", ephemeral=True
                )
                return None
            if not season_is_editable(media_log.season_year):
                await interaction.response.send_message(
                    closed_season_message(media_log.season_year), ephemeral=True
                )
                return None
            return media_log

        @self.tree.command(name="edit", description="Edit this Ghoul Bot media log")
        @app_commands.guilds(self.guild)
        async def edit(interaction: discord.Interaction) -> None:
            media_log = await owned_thread_log(interaction)
            if media_log is None:
                return
            await interaction.response.send_message(
                "What would you like to change?",
                view=EditChoiceView(
                    interaction.user.id,
                    media_log,
                    self.log_dependencies,
                ),
                ephemeral=True,
            )

        @self.tree.command(name="delete", description="Delete this post and its log history")
        @app_commands.guilds(self.guild)
        async def delete(interaction: discord.Interaction) -> None:
            media_log = await owned_thread_log(interaction)
            if media_log is None:
                return
            await interaction.response.send_message(
                "This permanently deletes the forum post **and removes it from your log "
                "history**. This cannot be undone. Continue?",
                view=DeleteConfirmationView(
                    interaction.user.id,
                    media_log,
                    self.log_dependencies,
                ),
                ephemeral=True,
            )

        @self.tree.command(name="mylogs", description="Browse and edit your media logs")
        @app_commands.guilds(self.guild)
        async def mylogs(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            results = await self.media_repository.list_owned_logs(
                interaction.user.id,
                limit=LOGS_PAGE_SIZE + 1,
            )
            media_logs = results[:LOGS_PAGE_SIZE]
            if not media_logs:
                await interaction.followup.send("You haven't logged any media yet.", ephemeral=True)
                return
            await interaction.followup.send(
                "Choose one of your logs — page 1:",
                view=LogsView(
                    interaction.user.id,
                    media_logs,
                    self.log_dependencies,
                    has_next=len(results) > LOGS_PAGE_SIZE,
                ),
                ephemeral=True,
            )

    async def setup_hook(self) -> None:
        """Register guild commands immediately in the staging server."""
        synced = await self.tree.sync(guild=self.guild)
        logger.info("Synced %d command(s) to guild %s", len(synced), self.guild.id)

    async def on_ready(self) -> None:
        """Log successful Gateway readiness without exposing credentials."""
        logger.info("Discord connected as %s", self.user)
