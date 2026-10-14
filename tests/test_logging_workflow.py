from datetime import date, datetime
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

import discord
import pytest

from media_club.bot import GhoulBot
from media_club.config import Settings
from media_club.log_ui import (
    LogDependencies,
    LogDetailsModal,
    ManualMediaModal,
    MovieArtworkView,
    MovieSearchModal,
    default_activity_date,
    parse_activity_date,
    rating_options,
)
from media_club.logging_workflow import (
    ForumPublisher,
    MediaDraft,
    display_title,
    log_content,
    log_embed,
    rating_stars,
)
from media_club.models import MediaItem, MediaLog
from media_club.tmdb import TmdbMovie


class FakeAvatar:
    url = "https://cdn.example/avatar.png"


class FakeUser:
    id = 42
    mention = "<@42>"
    display_name = "Ghoul Friend"
    display_avatar = FakeAvatar()


def test_optional_date_is_not_defaulted() -> None:
    assert parse_activity_date("") is None
    assert parse_activity_date("10/14/2026") == date(2026, 10, 14)
    assert parse_activity_date("1/4/26") == date(2026, 1, 4)
    assert parse_activity_date("12/31/99") == date(1999, 12, 31)
    with pytest.raises(ValueError):
        parse_activity_date("10-14-2026")
    with pytest.raises(ValueError):
        parse_activity_date("2/30/2026")
    with pytest.raises(ValueError):
        parse_activity_date("010/14/2026")


def test_activity_day_changes_at_four_am_chicago_time() -> None:
    chicago = ZoneInfo("America/Chicago")
    assert default_activity_date(datetime(2026, 10, 15, 3, 59, tzinfo=chicago)) == date(
        2026, 10, 14
    )
    assert default_activity_date(datetime(2026, 10, 15, 4, 0, tzinfo=chicago)) == date(2026, 10, 15)


def test_rating_options_are_strict_half_steps() -> None:
    options = rating_options()
    assert [option.value for option in options] == [
        "none",
        "1",
        "1.5",
        "2",
        "2.5",
        "3",
        "3.5",
        "4",
        "4.5",
        "5",
    ]


def test_movie_title_and_reviewer_snapshot_render_in_post() -> None:
    item = MediaItem(
        id=1,
        media_type="movie",
        provider="tmdb",
        provider_id="348",
        title="Alien",
        release_date=date(1979, 5, 25),
        provider_payload={},
    )
    media_log = MediaLog(
        id=7,
        media_item_id=1,
        discord_user_id=42,
        review="Still terrifying.",
        rating=Decimal("5.0"),
        activity_date=None,
        media_item=item,
    )

    embed = log_embed(
        cast(discord.User, FakeUser()),
        media_log,
        artwork_url="attachment://poster.jpg",
    )

    assert display_title(item) == "Alien (1979)"
    assert embed.author.name == "Ghoul Friend's log"
    assert embed.author.icon_url == "https://cdn.example/avatar.png"
    assert embed.description is None
    assert embed.fields[0].name == "Rating"
    assert embed.fields[0].value == "⭐⭐⭐⭐⭐ (5)"
    assert embed.fields[1].name == "Review"
    assert embed.fields[1].value == "Still terrifying."
    assert embed.image.url == "attachment://poster.jpg"
    assert "log #7" in (embed.footer.text or "")
    assert "Data & artwork from TMDB" in (embed.footer.text or "")
    assert log_content(cast(discord.User, FakeUser()), media_log) == "**Logged by <@42>**"


def test_half_star_rating_display() -> None:
    assert rating_stars(Decimal("3.5")) == "⭐⭐⭐½☆ (3.5)"


def test_modal_layouts_and_command_tree_construct() -> None:
    dependencies = LogDependencies(
        repository=cast(object, None),
        publisher=ForumPublisher(None, None),
        tmdb=None,
    )
    draft = MediaDraft(kind="book", title="")

    assert len(MovieSearchModal(dependencies).to_components()) == 1
    assert len(ManualMediaModal(draft, dependencies).to_components()) == 2
    assert len(LogDetailsModal(draft, dependencies).to_components()) == 4

    bot = GhoulBot(
        Settings(discord_application_id=1, discord_guild_id=2),
        cast(object, None),
        None,
    )
    commands = bot.tree.get_commands(guild=bot.guild)
    command_names = {command.name for command in commands}
    assert command_names == {"ping", "status", "about", "log", "edit", "delete", "mylogs"}
    for command_name in ("ping", "status"):
        operations_command = next(command for command in commands if command.name == command_name)
        assert operations_command.default_permissions is not None
        assert operations_command.default_permissions.manage_guild
    log_command = next(command for command in commands if command.name == "log")
    assert isinstance(log_command, discord.app_commands.Command)
    assert [parameter.name for parameter in log_command.parameters] == ["media_type", "title"]
    assert all(parameter.required for parameter in log_command.parameters)
    assert {choice.name for choice in log_command.parameters[0].choices} == {
        "Movies",
        "TV Shows",
        "Books",
        "Comics",
        "Podcasts",
        "Music",
        "Video Games",
        "Other",
    }


def test_movie_artwork_gallery_is_limited_to_first_five_posters() -> None:
    dependencies = LogDependencies(
        repository=cast(object, None),
        publisher=ForumPublisher(None, None),
        tmdb=None,
    )
    movie = TmdbMovie(
        id=348,
        title="Alien",
        original_title="Alien",
        release_date=date(1979, 5, 25),
        overview="In space no one can hear you scream.",
        poster_path="/default.jpg",
        payload={},
    )
    view = MovieArtworkView(
        42,
        movie,
        [f"/poster-{index}.jpg" for index in range(7)],
        dependencies,
    )

    gallery = next(child for child in view.children if isinstance(child, discord.ui.MediaGallery))
    assert len(gallery.items) == 5
