from media_club.database import async_database_url


def test_async_database_url_converts_postgres_scheme() -> None:
    assert (
        async_database_url("postgresql://user:pass@host/database")
        == "postgresql+asyncpg://user:pass@host/database"
    )


def test_async_database_url_preserves_asyncpg_scheme() -> None:
    url = "postgresql+asyncpg://user:pass@host/database"
    assert async_database_url(url) == url
