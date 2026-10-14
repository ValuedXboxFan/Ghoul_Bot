from dataclasses import dataclass
from typing import Literal

MediaKind = Literal[
    "movie",
    "tv_show",
    "book",
    "comic",
    "podcast",
    "music",
    "video_game",
    "other",
]


@dataclass(frozen=True, slots=True)
class MediaTypeDefinition:
    """Discord command and forum presentation for a media type."""

    key: MediaKind
    label: str
    tag_name: str


MEDIA_TYPES: dict[MediaKind, MediaTypeDefinition] = {
    "movie": MediaTypeDefinition("movie", "Movie", "Movies"),
    "tv_show": MediaTypeDefinition("tv_show", "TV Show", "TV Shows"),
    "book": MediaTypeDefinition("book", "Book", "Books"),
    "comic": MediaTypeDefinition("comic", "Comic", "Comics"),
    "podcast": MediaTypeDefinition("podcast", "Podcast", "Podcasts"),
    "music": MediaTypeDefinition("music", "Music", "Music"),
    "video_game": MediaTypeDefinition("video_game", "Video Game", "Video Games"),
    "other": MediaTypeDefinition("other", "Other", "Other"),
}
