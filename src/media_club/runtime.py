import asyncio
import logging

from media_club.bot import GhoulBot
from media_club.config import Settings
from media_club.database import Database

logger = logging.getLogger(__name__)


class ApplicationRuntime:
    """Coordinate database and Discord resources with the web lifecycle."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.database: Database | None = None
        self.bot: GhoulBot | None = None
        self.bot_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Initialize configured dependencies without blocking HTTP startup."""
        if self.config.database_url:
            self.database = Database(self.config.database_url)
        else:
            logger.warning("DATABASE_URL is not configured")

        if self.config.discord_configured and self.database is not None:
            token = self.config.discord_bot_token
            if token is None:
                return
            self.bot = GhoulBot(self.config, self.database)
            self.bot_task = asyncio.create_task(
                self.bot.start(token.get_secret_value()),
                name="discord-client",
            )
            self.bot_task.add_done_callback(self._log_bot_exit)
        else:
            logger.warning("Discord is not fully configured; bot client was not started")

    def _log_bot_exit(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error(
                "Discord client stopped unexpectedly",
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    async def stop(self) -> None:
        """Close Discord and database resources cleanly."""
        if self.bot is not None and not self.bot.is_closed():
            await self.bot.close()
        if self.bot_task is not None:
            await asyncio.gather(self.bot_task, return_exceptions=True)
        if self.database is not None:
            await self.database.close()

    async def readiness(self) -> tuple[bool, dict[str, str]]:
        """Check database connectivity and Discord Gateway readiness."""
        database_status = "not_configured"
        if self.database is not None:
            database_status = "connected" if await self.database.ping() else "unavailable"

        discord_status = "not_configured"
        if self.bot is not None:
            discord_status = "connected" if self.bot.is_ready() else "connecting"

        checks = {"database": database_status, "discord": discord_status}
        return all(value == "connected" for value in checks.values()), checks
