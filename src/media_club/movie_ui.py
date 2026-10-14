import discord

from media_club.repositories import MediaRepository
from media_club.tmdb import TmdbMovie

TMDB_ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."


def movie_embed(movie: TmdbMovie) -> discord.Embed:
    """Build a consistent Discord presentation for a TMDB movie."""
    embed = discord.Embed(
        title=movie.display_title,
        description=movie.overview or "No overview is available.",
        url=f"https://www.themoviedb.org/movie/{movie.id}",
        color=discord.Color.dark_teal(),
    )
    if movie.poster_url:
        embed.set_thumbnail(url=movie.poster_url)
    embed.set_footer(text=TMDB_ATTRIBUTION)
    return embed


class MovieSearchView(discord.ui.View):
    """Ephemeral picker for a set of movie search results."""

    def __init__(self, movies: list[TmdbMovie], repository: MediaRepository) -> None:
        super().__init__(timeout=180)
        self.add_item(MovieSearchSelect(movies, repository))


class MovieSearchSelect(discord.ui.Select[MovieSearchView]):
    """Select control that persists and displays the chosen movie."""

    def __init__(self, movies: list[TmdbMovie], repository: MediaRepository) -> None:
        self.movies = {str(movie.id): movie for movie in movies}
        self.repository = repository
        options = [
            discord.SelectOption(
                label=movie.display_title[:100],
                value=str(movie.id),
                description=(movie.overview or "No overview available")[:100],
            )
            for movie in movies
        ]
        super().__init__(
            placeholder="Choose the correct movie",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Save the selected result and replace the picker with its details."""
        movie = self.movies[self.values[0]]
        item = await self.repository.save_tmdb_movie(movie)
        await interaction.response.edit_message(
            content=f"Saved **{movie.display_title}** as media item `{item.id}`.",
            embed=movie_embed(movie),
            view=None,
        )
