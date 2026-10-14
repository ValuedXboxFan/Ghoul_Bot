from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

TMDB_API_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_URL = "https://image.tmdb.org/t/p/w500"


class TmdbError(RuntimeError):
    """Raised when TMDB cannot satisfy a request."""


@dataclass(frozen=True, slots=True)
class TmdbMovie:
    """Movie search result normalized from TMDB."""

    id: int
    title: str
    original_title: str
    release_date: date | None
    overview: str
    poster_path: str | None
    payload: dict[str, Any]

    @property
    def year(self) -> str:
        """Return the release year or a useful placeholder."""
        return str(self.release_date.year) if self.release_date else "TBA"

    @property
    def display_title(self) -> str:
        """Return a compact title suitable for Discord controls."""
        return f"{self.title} ({self.year})"

    @property
    def poster_url(self) -> str | None:
        """Return the TMDB poster CDN URL when artwork is available."""
        return f"{TMDB_IMAGE_URL}{self.poster_path}" if self.poster_path else None


class TmdbClient:
    """Small asynchronous client for the TMDB application API."""

    def __init__(
        self,
        access_token: str,
        *,
        language: str = "en-US",
        region: str = "US",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.access_token = access_token
        self.language = language
        self.region = region
        self.http = http_client or httpx.AsyncClient(
            base_url=TMDB_API_URL,
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

    async def search_movies(self, query: str, *, limit: int = 10) -> list[TmdbMovie]:
        """Search TMDB and return normalized movie results."""
        clean_query = query.strip()
        if not clean_query:
            return []

        try:
            response = await self.http.get(
                "/search/movie",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={
                    "query": clean_query,
                    "language": self.language,
                    "region": self.region,
                    "include_adult": "false",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise TmdbError("TMDB movie search failed") from error

        payload = response.json()
        if not isinstance(payload, dict):
            raise TmdbError("TMDB returned an unexpected response")
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise TmdbError("TMDB returned an unexpected result list")

        movies: list[TmdbMovie] = []
        for raw_movie in raw_results:
            if isinstance(raw_movie, dict):
                movie = _parse_movie(raw_movie)
                if movie is not None:
                    movies.append(movie)
            if len(movies) >= limit:
                break
        return movies

    async def download_poster(self, movie: TmdbMovie) -> bytes | None:
        """Download poster artwork for upload to Discord."""
        return await self.download_poster_path(movie.poster_path)

    async def download_poster_path(self, poster_path: str | None) -> bytes | None:
        """Download poster artwork from a stored TMDB poster path."""
        if poster_path is None:
            return None

        try:
            response = await self.http.get(f"{TMDB_IMAGE_URL}{poster_path}")
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise TmdbError("TMDB poster download failed") from error
        return response.content

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self.http.aclose()


def _parse_movie(payload: dict[str, Any]) -> TmdbMovie | None:
    movie_id = payload.get("id")
    title = payload.get("title")
    if not isinstance(movie_id, int) or not isinstance(title, str) or not title:
        return None

    raw_release_date = payload.get("release_date")
    release_date: date | None = None
    if isinstance(raw_release_date, str) and raw_release_date:
        try:
            release_date = date.fromisoformat(raw_release_date)
        except ValueError:
            release_date = None

    original_title = payload.get("original_title")
    overview = payload.get("overview")
    poster_path = payload.get("poster_path")
    return TmdbMovie(
        id=movie_id,
        title=title,
        original_title=original_title if isinstance(original_title, str) else title,
        release_date=release_date,
        overview=overview if isinstance(overview, str) else "",
        poster_path=poster_path if isinstance(poster_path, str) else None,
        payload=payload,
    )
