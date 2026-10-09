import logging

import discord
from discord import app_commands

from media_club.config import Settings
from media_club.database import Database

logger = logging.getLogger(__name__)


class GhoulBot(discord.Client):
    """Discord client and application-command registry."""

    def __init__(self, config: Settings, database: Database) -> None:
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
            await interaction.response.send_message(
                f"Environment: **{config.app_env}**\nDatabase: **{database_status}**",
                ephemeral=True,
            )

    async def setup_hook(self) -> None:
        """Register guild commands immediately in the staging server."""
        synced = await self.tree.sync(guild=self.guild)
        logger.info("Synced %d command(s) to guild %s", len(synced), self.guild.id)

    async def on_ready(self) -> None:
        """Log successful Gateway readiness without exposing credentials."""
        logger.info("Discord connected as %s", self.user)
