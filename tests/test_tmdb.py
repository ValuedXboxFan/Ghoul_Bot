import asyncio

import httpx

from media_club.tmdb import TmdbClient


def test_search_movies_normalizes_results_and_authenticates() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer secret-token"
            assert request.url.params["query"] == "Alien"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 348,
                            "title": "Alien",
                            "original_title": "Alien",
                            "release_date": "1979-05-25",
                            "overview": "In space no one can hear you scream.",
                            "poster_path": "/poster.jpg",
                        }
                    ]
                },
            )

        http_client = httpx.AsyncClient(
            base_url="https://api.themoviedb.org/3",
            headers={"Authorization": "Bearer secret-token"},
            transport=httpx.MockTransport(handler),
        )
        client = TmdbClient("secret-token", http_client=http_client)
        movies = await client.search_movies("Alien")
        await client.close()

        assert len(movies) == 1
        assert movies[0].id == 348
        assert movies[0].display_title == "Alien (1979)"
        assert movies[0].poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"

    asyncio.run(scenario())


def test_search_movies_ignores_malformed_results() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"results": [{"title": "Missing ID"}, {"id": 1}]},
            )
        )
        client = TmdbClient(
            "secret-token",
            http_client=httpx.AsyncClient(
                base_url="https://api.themoviedb.org/3",
                transport=transport,
            ),
        )
        movies = await client.search_movies("bad data")
        await client.close()
        assert movies == []

    asyncio.run(scenario())
