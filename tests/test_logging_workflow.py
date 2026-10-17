import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import discord
import pytest

from media_club.bot import GhoulBot
from media_club.config import Settings
from media_club.log_ui import (
    LogDependencies,
    LogDetailsModal,
    ManualMediaModal,
    MediaReadyView,
    MovieArtworkView,
    MovieSearchModal,
    default_activity_date,
    open_modal_and_dismiss_prompt,
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


def test_media_details_update_existing_forum_post_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = MediaItem(
        id=2,
        media_type="movie",
        provider="manual",
        provider_id=None,
        title="Alien: The Director's Cut",
        release_date=None,
        provider_payload={},
    )
    media_log = MediaLog(
        id=7,
        media_item_id=1,
        discord_user_id=42,
        review="Still terrifying.",
        rating=Decimal("5.0"),
        activity_date=None,
        forum_thread_id=100,
        forum_message_id=101,
        media_item=item,
    )
    tag = discord.ForumTag(name="Movies")
    forum = AsyncMock(spec=discord.ForumChannel)
    forum.available_tags = [tag]
    message = AsyncMock(spec=discord.Message)

    class FakeThread:
        def __init__(self) -> None:
            self.edit = AsyncMock()
            self.fetch_message = AsyncMock(return_value=message)

    monkeypatch.setattr("media_club.logging_workflow.discord.Thread", FakeThread)
    thread = FakeThread()
    client = SimpleNamespace(get_channel=lambda channel_id: thread)
    publisher = ForumPublisher(99, None)
    publisher._forum = AsyncMock(return_value=forum)
    publisher.artwork = AsyncMock(return_value=None)

    asyncio.run(
        publisher.update_media_post(
            cast(discord.Client, client),
            cast(discord.User, FakeUser()),
            media_log,
            item,
        )
    )

    thread.edit.assert_awaited_once_with(
        name="Alien: The Director's Cut",
        archived=False,
        applied_tags=[tag],
        reason="Owner updated Ghoul Bot media details",
    )
    thread.fetch_message.assert_awaited_once_with(101)
    message.edit.assert_awaited_once()
    assert message.edit.await_args.kwargs["attachments"] == []
    assert message.edit.await_args.kwargs["content"] == "**Logged by <@42>**"


def test_opening_next_modal_dismisses_the_completed_prompt() -> None:
    prompt = SimpleNamespace(delete=AsyncMock())
    response = SimpleNamespace(send_modal=AsyncMock())
    interaction = cast(
        discord.Interaction,
        SimpleNamespace(message=prompt, response=response),
    )
    modal = MovieSearchModal(
        LogDependencies(
            repository=cast(object, None),
            publisher=ForumPublisher(None, None),
            tmdb=None,
        )
    )

    asyncio.run(open_modal_and_dismiss_prompt(interaction, modal))

    response.send_modal.assert_awaited_once_with(modal)
    prompt.delete.assert_awaited_once_with()


def test_modal_layouts_and_command_tree_construct() -> None:
    dependencies = LogDependencies(
        repository=cast(object, None),
        publisher=ForumPublisher(None, None),
        tmdb=None,
    )
    draft = MediaDraft(kind="book", title="")

    assert len(MovieSearchModal(dependencies).to_components()) == 1
    assert len(ManualMediaModal(draft, dependencies).to_components()) == 2
    assert len(LogDetailsModal(draft, dependencies, publish=False).to_components()) == 3

    ready_view = MediaReadyView(42, draft, dependencies, publish=False)
    ready_text = next(
        item for item in ready_view.children if isinstance(item, discord.ui.TextDisplay)
    )
    ready_buttons = [
        component
        for item in ready_view.children
        if isinstance(item, discord.ui.ActionRow)
        for component in item.children
    ]
    assert "private ledger" in ready_text.content
    assert [button.label for button in ready_buttons] == ["Add log details", "Cancel"]
    assert ready_buttons[1].style is discord.ButtonStyle.danger

    post_ready_view = MediaReadyView(42, draft, dependencies, publish=True)
    post_ready_text = next(
        item for item in post_ready_view.children if isinstance(item, discord.ui.TextDisplay)
    )
    assert "create a forum post" in post_ready_text.content

    bot = GhoulBot(
        Settings(discord_application_id=1, discord_guild_id=2),
        cast(object, None),
        None,
    )
    commands = bot.tree.get_commands(guild=bot.guild)
    command_names = {command.name for command in commands}
    assert command_names == {
        "ping",
        "status",
        "about",
        "log",
        "post",
        "edit",
        "delete",
        "mylogs",
    }
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
    post_command = next(command for command in commands if command.name == "post")
    assert isinstance(post_command, discord.app_commands.Command)
    assert [parameter.name for parameter in post_command.parameters] == ["media_type", "title"]
    assert all(parameter.required for parameter in post_command.parameters)


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
        publish=True,
    )

    gallery = next(child for child in view.children if isinstance(child, discord.ui.MediaGallery))
    assert len(gallery.items) == 5
