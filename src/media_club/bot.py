import logging

import discord
from discord import app_commands

from media_club.config import Settings
from media_club.database import Database
from media_club.log_ui import (
    DeleteConfirmationView,
    EditChoiceView,
    LogDependencies,
    LogsView,
    ManualMediaModal,
    MovieSearchModal,
)
from media_club.logging_workflow import ForumPublisher, MediaDraft
from media_club.media_types import MediaKind
from media_club.models import MediaLog
from media_club.repositories import MediaRepository
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

        log_group = app_commands.Group(name="log", description="Log media you've experienced")

        @log_group.command(name="movie", description="Log a movie")
        async def log_movie(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(MovieSearchModal(self.log_dependencies))

        async def start_manual_log(
            interaction: discord.Interaction,
            media_kind: MediaKind,
        ) -> None:
            await interaction.response.send_modal(
                ManualMediaModal(
                    MediaDraft(kind=media_kind, title=""),
                    self.log_dependencies,
                )
            )

        @log_group.command(name="tv_show", description="Log a TV show")
        async def log_tv_show(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "tv_show")

        @log_group.command(name="book", description="Log a book")
        async def log_book(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "book")

        @log_group.command(name="comic", description="Log a comic")
        async def log_comic(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "comic")

        @log_group.command(name="podcast", description="Log a podcast")
        async def log_podcast(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "podcast")

        @log_group.command(name="music", description="Log music")
        async def log_music(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "music")

        @log_group.command(name="video_game", description="Log a video game")
        async def log_video_game(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "video_game")

        @log_group.command(name="other", description="Log another kind of media")
        async def log_other(interaction: discord.Interaction) -> None:
            await start_manual_log(interaction, "other")

        self.tree.add_command(log_group, guild=self.guild)

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

        @self.tree.command(name="logs", description="Manage logs without forum posts")
        @app_commands.guilds(self.guild)
        async def logs(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            media_logs = await self.media_repository.list_unposted_logs(interaction.user.id)
            if not media_logs:
                await interaction.followup.send(
                    "You don't have any private logs without forum posts.", ephemeral=True
                )
                return
            await interaction.followup.send(
                "Choose one of your private logs:",
                view=LogsView(
                    interaction.user.id,
                    media_logs,
                    self.log_dependencies,
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
