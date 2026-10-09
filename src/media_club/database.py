from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def async_database_url(url: str) -> str:
    """Convert a Railway/Postgres URL to SQLAlchemy's asyncpg dialect."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


class Database:
    """Own the database engine and lightweight operational checks."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            async_database_url(url),
            pool_pre_ping=True,
        )

    async def ping(self) -> bool:
        """Check that PostgreSQL accepts a simple query."""
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def close(self) -> None:
        """Dispose of pooled connections."""
        await self.engine.dispose()
