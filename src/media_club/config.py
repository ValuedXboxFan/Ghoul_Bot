from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: str | None = None
    discord_bot_token: SecretStr | None = None
    discord_application_id: int | None = None
    discord_guild_id: int | None = None

    @property
    def discord_configured(self) -> bool:
        """Return whether all settings required to start Discord are present."""
        return all(
            (
                self.discord_bot_token,
                self.discord_application_id,
                self.discord_guild_id,
            )
        )


settings = Settings()
