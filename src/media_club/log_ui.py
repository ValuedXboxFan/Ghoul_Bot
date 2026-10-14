from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import discord
from sqlalchemy.exc import SQLAlchemyError

from media_club.logging_workflow import (
    MAX_ARTWORK_BYTES,
    ForumConfigurationError,
    ForumPublisher,
    LogData,
    MediaDraft,
)
from media_club.media_types import MEDIA_TYPES
from media_club.models import MediaItem, MediaLog
from media_club.repositories import MediaRepository
from media_club.tmdb import TMDB_IMAGE_URL, TmdbClient, TmdbError, TmdbMovie

logger = logging.getLogger(__name__)

RATINGS = tuple(Decimal(value) / 2 for value in range(2, 11))
ACTIVITY_TIMEZONE = ZoneInfo("America/Chicago")
LOGS_PAGE_SIZE = 25


@dataclass(frozen=True, slots=True)
class LogDependencies:
    """Services shared by the short-lived Discord workflow components."""

    repository: MediaRepository
    publisher: ForumPublisher
    tmdb: TmdbClient | None


def rating_options(selected: Decimal | None = None) -> list[discord.SelectOption]:
    """Build the strict 1–5 half-step rating choices."""
    return [
        discord.SelectOption(
            label="No rating",
            value="none",
            default=selected is None,
        ),
        *[
            discord.SelectOption(
                label=f"{rating.normalize()}",
                value=str(rating),
                default=rating == selected,
            )
            for rating in RATINGS
        ],
    ]


def default_activity_date(now: datetime | None = None) -> date:
    """Return the server's activity day, whose boundary is 4 AM Chicago time."""
    local_now = now.astimezone(ACTIVITY_TIMEZONE) if now else datetime.now(ACTIVITY_TIMEZONE)
    return (local_now - timedelta(hours=4)).date()


def format_activity_date(value: date) -> str:
    """Render a date using the member-facing month/day/year format."""
    return f"{value.month}/{value.day}/{value.year}"


def parse_activity_date(value: str) -> date | None:
    """Parse M/D/YY or M/D/YYYY while preserving an intentionally blank value."""
    clean = value.strip()
    if not clean:
        return None
    parts = clean.split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("Use Month/Day/Year format")
    month, day, year = parts
    if not 1 <= len(month) <= 2 or not 1 <= len(day) <= 2 or len(year) not in (2, 4):
        raise ValueError("Use Month/Day/Year format")
    year_value = int(year)
    if len(year) == 2:
        year_value += 2000 if year_value <= 49 else 1900
    return date(year_value, int(month), int(day))


def selected_rating(select: discord.ui.Select[discord.ui.Modal]) -> Decimal | None:
    """Return the validated selection from a constrained modal select."""
    if not select.values:
        return None
    if select.values[0] == "none":
        return None
    rating = Decimal(select.values[0])
    if rating not in RATINGS:
        raise ValueError("Invalid rating")
    return rating


async def persist_draft(dependencies: LogDependencies, draft: MediaDraft) -> MediaItem:
    """Persist a provider result or a manual item."""
    if draft.tmdb_movie is not None:
        return await dependencies.repository.save_tmdb_movie(draft.tmdb_movie)
    return await dependencies.repository.save_manual_item(
        draft.kind,
        draft.title.strip(),
        artwork_data=draft.artwork_data,
        artwork_content_type=draft.artwork_content_type,
        artwork_filename=draft.artwork_filename,
    )


class OwnedView(discord.ui.View):
    """Base ephemeral view restricted to the member who started it."""

    def __init__(self, owner_id: int, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "This control belongs to another member.", ephemeral=True
        )
        return False


class OwnedLayoutView(discord.ui.LayoutView):
    """Base Components V2 layout restricted to the member who started it."""

    def __init__(self, owner_id: int, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "This control belongs to another member.", ephemeral=True
        )
        return False


class MovieLogStartView(OwnedView):
    """Let a member choose lookup or manual entry before searching TMDB."""

    def __init__(self, owner_id: int, title: str, dependencies: LogDependencies) -> None:
        super().__init__(owner_id)
        self.title = title
        self.dependencies = dependencies

    @discord.ui.button(label="Search TMDB", style=discord.ButtonStyle.primary, emoji="🔎")
    async def search(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[MovieLogStartView],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await present_movie_results(interaction, self.title, self.dependencies)

    @discord.ui.button(label="Enter manually", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def manual(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[MovieLogStartView],
    ) -> None:
        await interaction.response.send_modal(
            ManualLogModal(MediaDraft(kind="movie", title=self.title), self.dependencies)
        )


class MovieLookupView(OwnedView):
    """Choose a TMDB movie result."""

    def __init__(
        self,
        owner_id: int,
        query: str,
        movies: list[TmdbMovie],
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.add_item(MovieLookupSelect(query, movies, dependencies))


class MovieSearchModal(discord.ui.Modal):
    """Collect a movie query before presenting TMDB results."""

    def __init__(self, dependencies: LogDependencies) -> None:
        super().__init__(title="Find a movie", timeout=600)
        self.dependencies = dependencies
        self.query_input: discord.ui.TextInput[MovieSearchModal] = discord.ui.TextInput(
            placeholder="Movie title",
            min_length=1,
            max_length=100,
        )
        self.add_item(discord.ui.Label(text="Title", component=self.query_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = str(self.query_input).strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        await present_movie_results(interaction, query, self.dependencies)


async def present_movie_results(
    interaction: discord.Interaction,
    query: str,
    dependencies: LogDependencies,
) -> None:
    """Search TMDB and display results after an interaction has been deferred."""
    if dependencies.tmdb is None:
        await interaction.followup.send(
            "Movie lookup is unavailable. You can still enter it manually.",
            view=ManualStartView(
                interaction.user.id,
                MediaDraft(kind="movie", title=query),
                dependencies,
            ),
            ephemeral=True,
        )
        return
    try:
        movies = await dependencies.tmdb.search_movies(query)
    except TmdbError:
        logger.exception("TMDB lookup failed during movie log")
        movies = []
    if not movies:
        await interaction.followup.send(
            "No useful movie results were found. You can enter it manually.",
            view=ManualStartView(
                interaction.user.id,
                MediaDraft(kind="movie", title=query),
                dependencies,
            ),
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        "Choose the correct movie:",
        view=MovieLookupView(
            interaction.user.id,
            query,
            movies,
            dependencies,
        ),
        ephemeral=True,
    )


class ManualStartView(OwnedView):
    """Offer manual media entry after an unavailable or unsuccessful lookup."""

    def __init__(
        self,
        owner_id: int,
        draft: MediaDraft,
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.draft = draft
        self.dependencies = dependencies

    @discord.ui.button(label="Enter manually", style=discord.ButtonStyle.primary)
    async def enter_manually(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[ManualStartView],
    ) -> None:
        await interaction.response.send_modal(ManualLogModal(self.draft, self.dependencies))


class MovieLookupSelect(discord.ui.Select[MovieLookupView]):
    """Movie result picker followed by artwork selection."""

    def __init__(
        self,
        query: str,
        movies: list[TmdbMovie],
        dependencies: LogDependencies,
    ) -> None:
        self.query = query
        self.movies = {str(movie.id): movie for movie in movies}
        self.dependencies = dependencies
        options = [
            discord.SelectOption(
                label=movie.display_title[:100],
                value=str(movie.id),
                description=(movie.overview or "No overview available")[:100],
            )
            for movie in movies[:25]
        ]
        super().__init__(
            placeholder="Choose the correct movie",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        movie = self.movies[self.values[0]]
        await interaction.response.defer(ephemeral=True, thinking=True)
        poster_paths = [movie.poster_path] if movie.poster_path else []
        if self.dependencies.tmdb is not None:
            try:
                discovered = await self.dependencies.tmdb.movie_poster_paths(movie.id)
                poster_paths.extend(path for path in discovered if path not in poster_paths)
            except TmdbError:
                logger.warning("Could not load alternate TMDB posters", exc_info=True)

        view = MovieArtworkView(
            interaction.user.id,
            movie,
            poster_paths,
            self.dependencies,
        )
        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=view,
        )


class MovieArtworkSelect(discord.ui.Select["MovieArtworkView"]):
    def __init__(self, poster_paths: list[str]) -> None:
        options = [
            discord.SelectOption(label="No artwork", value="none"),
            *[
                discord.SelectOption(
                    label=f"Poster {index + 1}",
                    value=str(index),
                    default=index == 0,
                )
                for index in range(len(poster_paths))
            ],
        ]
        super().__init__(placeholder="Choose poster artwork", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:
            return
        self.view.selected_index = None if self.values[0] == "none" else int(self.values[0])
        for option in self.options:
            option.default = option.value == self.values[0]
        self.view.update_instructions()
        await interaction.response.edit_message(view=self.view)


class MovieArtworkContinueButton(discord.ui.Button["MovieArtworkView"]):
    def __init__(self) -> None:
        super().__init__(label="Continue", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:
            return
        await self.view.continue_log(interaction)


class MovieArtworkView(OwnedLayoutView):
    """Show five TMDB posters together and retain the member's selection."""

    def __init__(
        self,
        owner_id: int,
        movie: TmdbMovie,
        poster_paths: list[str],
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.movie = movie
        self.poster_paths = poster_paths[:5]
        self.selected_index: int | None = 0 if self.poster_paths else None
        self.dependencies = dependencies
        self.instructions: discord.ui.TextDisplay[MovieArtworkView] = discord.ui.TextDisplay("")
        self.update_instructions()
        self.add_item(self.instructions)
        if self.poster_paths:
            self.add_item(
                discord.ui.MediaGallery(
                    *[
                        discord.MediaGalleryItem(
                            f"{TMDB_IMAGE_URL}{path}",
                            description=f"Poster {index + 1} for {movie.display_title}",
                        )
                        for index, path in enumerate(self.poster_paths)
                    ]
                )
            )
        self.add_item(discord.ui.ActionRow(MovieArtworkSelect(self.poster_paths)))
        self.add_item(discord.ui.ActionRow(MovieArtworkContinueButton()))

    def update_instructions(self) -> None:
        selected = (
            f"Poster {self.selected_index + 1} selected."
            if self.selected_index is not None
            else "No artwork selected."
        )
        self.instructions.content = (
            f"## {self.movie.display_title}\n"
            "Choose from the first five available posters, numbered left-to-right and "
            f"top-to-bottom. **{selected}**"
        )

    async def continue_log(
        self,
        interaction: discord.Interaction,
    ) -> None:
        poster_path = (
            self.poster_paths[self.selected_index] if self.selected_index is not None else None
        )
        await interaction.response.send_modal(
            LogDetailsModal(
                MediaDraft(
                    kind="movie",
                    title=self.movie.title,
                    tmdb_movie=self.movie,
                    selected_poster_path=poster_path,
                ),
                self.dependencies,
            )
        )


class ManualLogModal(discord.ui.Modal):
    """Collect manual artwork and member log details in one modal."""

    def __init__(
        self,
        draft: MediaDraft,
        dependencies: LogDependencies,
        *,
        initial: LogData | None = None,
        publish: bool = True,
    ) -> None:
        super().__init__(title=f"Log {MEDIA_TYPES[draft.kind].label}"[:45], timeout=600)
        self.draft = draft
        self.dependencies = dependencies
        self.artwork_input: discord.ui.FileUpload[ManualLogModal] = discord.ui.FileUpload(
            required=False,
            min_values=0,
            max_values=1,
        )
        self.review_input: discord.ui.TextInput[ManualLogModal] = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=initial.review if initial and initial.review else None,
            placeholder="What did you think?",
            required=False,
            max_length=1800,
        )
        self.rating_input: discord.ui.Select[ManualLogModal] = discord.ui.Select(
            placeholder="Select a rating",
            options=rating_options(initial.rating if initial else None),
            min_values=1,
            max_values=1,
            required=True,
        )
        initial_date = initial.activity_date if initial is not None else default_activity_date()
        self.date_input: discord.ui.TextInput[ManualLogModal] = discord.ui.TextInput(
            default=format_activity_date(initial_date) if initial_date else None,
            placeholder="Month/Day/Year",
            required=False,
            max_length=10,
        )
        self.publish_input: discord.ui.Select[ManualLogModal] = discord.ui.Select(
            placeholder="Create a forum post?",
            options=[
                discord.SelectOption(label="Yes", value="yes", default=publish),
                discord.SelectOption(label="No", value="no", default=not publish),
            ],
            min_values=1,
            max_values=1,
            required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="Artwork (optional)",
                description="PNG, JPEG, GIF, or WebP; maximum 5 MB",
                component=self.artwork_input,
            )
        )
        self.add_item(discord.ui.Label(text="Review (optional)", component=self.review_input))
        self.add_item(discord.ui.Label(text="Rating (optional)", component=self.rating_input))
        self.add_item(
            discord.ui.Label(
                text="Date (optional)",
                description="Month/Day/Year; leave blank to record no date",
                component=self.date_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Create forum post?",
                description="Choose No to save only in your log history",
                component=self.publish_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self._apply_artwork()
            log_data = LogData(
                review=str(self.review_input).strip() or None,
                rating=selected_rating(self.rating_input),
                activity_date=parse_activity_date(str(self.date_input)),
            )
        except ValueError as error:
            await interaction.response.send_message(
                str(error),
                view=ManualLogRetryView(
                    interaction.user.id,
                    self.draft,
                    self.dependencies,
                    LogData(
                        review=str(self.review_input).strip() or None,
                        rating=selected_rating(self.rating_input),
                        activity_date=None,
                    ),
                    self.publish_input.values[0] == "yes",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await save_new_log(
            interaction,
            self.draft,
            log_data,
            self.dependencies,
            publish=self.publish_input.values[0] == "yes",
        )

    async def _apply_artwork(self) -> None:
        if not self.artwork_input.values:
            return
        attachment = self.artwork_input.values[0]
        content_type = attachment.content_type or ""
        if not content_type.startswith("image/"):
            raise ValueError("Artwork must be an image file.")
        if attachment.size > MAX_ARTWORK_BYTES:
            raise ValueError("Artwork must be 5 MB or smaller.")
        self.draft.artwork_data = await attachment.read(use_cached=True)
        self.draft.artwork_content_type = content_type
        self.draft.artwork_filename = attachment.filename


class ManualLogRetryView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        draft: MediaDraft,
        dependencies: LogDependencies,
        initial: LogData,
        publish: bool,
    ) -> None:
        super().__init__(owner_id)
        self.draft = draft
        self.dependencies = dependencies
        self.initial = initial
        self.publish = publish

    @discord.ui.button(label="Try again", style=discord.ButtonStyle.primary)
    async def retry(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[ManualLogRetryView],
    ) -> None:
        await interaction.response.send_modal(
            ManualLogModal(
                self.draft,
                self.dependencies,
                initial=self.initial,
                publish=self.publish,
            )
        )


class ManualMediaModal(discord.ui.Modal):
    """Collect a replacement manual title and optional artwork."""

    def __init__(
        self,
        draft: MediaDraft,
        dependencies: LogDependencies,
        *,
        replacement_log_id: int,
    ) -> None:
        super().__init__(title=f"{MEDIA_TYPES[draft.kind].label} details"[:45], timeout=600)
        self.draft = draft
        self.dependencies = dependencies
        self.replacement_log_id = replacement_log_id
        self.title_input: discord.ui.TextInput[ManualMediaModal] = discord.ui.TextInput(
            default=draft.title[:100],
            placeholder="Title",
            min_length=1,
            max_length=100,
        )
        self.artwork_input: discord.ui.FileUpload[ManualMediaModal] = discord.ui.FileUpload(
            required=False,
            min_values=0,
            max_values=1,
        )
        self.add_item(discord.ui.Label(text="Title", component=self.title_input))
        self.add_item(
            discord.ui.Label(
                text="Artwork (optional)",
                description="PNG, JPEG, GIF, or WebP; maximum 5 MB",
                component=self.artwork_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self._apply_inputs()
        except ValueError as error:
            await interaction.response.send_message(
                str(error),
                view=ManualRetryView(
                    interaction.user.id,
                    self.draft,
                    self.dependencies,
                    self.replacement_log_id,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await replace_media_details(
            interaction,
            self.replacement_log_id,
            self.draft,
            self.dependencies,
        )

    async def _apply_inputs(self) -> None:
        self.draft.title = str(self.title_input).strip()
        if not self.draft.title:
            raise ValueError("A title is required.")
        if not self.artwork_input.values:
            return
        attachment = self.artwork_input.values[0]
        content_type = attachment.content_type or ""
        if not content_type.startswith("image/"):
            raise ValueError("Artwork must be an image file.")
        if attachment.size > MAX_ARTWORK_BYTES:
            raise ValueError("Artwork must be 5 MB or smaller.")
        self.draft.artwork_data = await attachment.read(use_cached=True)
        self.draft.artwork_content_type = content_type
        self.draft.artwork_filename = attachment.filename


class ManualRetryView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        draft: MediaDraft,
        dependencies: LogDependencies,
        replacement_log_id: int,
    ) -> None:
        super().__init__(owner_id)
        self.draft = draft
        self.dependencies = dependencies
        self.replacement_log_id = replacement_log_id

    @discord.ui.button(label="Try again", style=discord.ButtonStyle.primary)
    async def retry(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[ManualRetryView],
    ) -> None:
        await interaction.response.send_modal(
            ManualMediaModal(
                self.draft,
                self.dependencies,
                replacement_log_id=self.replacement_log_id,
            )
        )


class LogDetailsModal(discord.ui.Modal):
    """Collect member log data and whether to create a forum post."""

    def __init__(
        self,
        draft: MediaDraft,
        dependencies: LogDependencies,
        *,
        initial: LogData | None = None,
        publish: bool = True,
    ) -> None:
        super().__init__(title="Your log", timeout=600)
        self.draft = draft
        self.dependencies = dependencies
        self.review_input: discord.ui.TextInput[LogDetailsModal] = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=initial.review if initial and initial.review else None,
            placeholder="What did you think?",
            required=False,
            max_length=1800,
        )
        self.rating_input: discord.ui.Select[LogDetailsModal] = discord.ui.Select(
            placeholder="Select a rating",
            options=rating_options(initial.rating if initial else None),
            min_values=1,
            max_values=1,
            required=True,
        )
        initial_date = initial.activity_date if initial is not None else default_activity_date()
        self.date_input: discord.ui.TextInput[LogDetailsModal] = discord.ui.TextInput(
            default=format_activity_date(initial_date) if initial_date else None,
            placeholder="Month/Day/Year",
            required=False,
            max_length=10,
        )
        self.publish_input: discord.ui.Select[LogDetailsModal] = discord.ui.Select(
            placeholder="Create a forum post?",
            options=[
                discord.SelectOption(label="Yes", value="yes", default=publish),
                discord.SelectOption(label="No", value="no", default=not publish),
            ],
            min_values=1,
            max_values=1,
            required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="Review (optional)",
                description="Share as much or as little as you'd like",
                component=self.review_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Rating (optional)",
                description="Ratings use a strict 1–5 scale in half-point steps",
                component=self.rating_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Date (optional)",
                description="Month/Day/Year; leave blank to record no date",
                component=self.date_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Create forum post?",
                description="Choose No to save only in your log history",
                component=self.publish_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            log_data = LogData(
                review=str(self.review_input).strip() or None,
                rating=selected_rating(self.rating_input),
                activity_date=parse_activity_date(str(self.date_input)),
            )
        except ValueError:
            await interaction.response.send_message(
                "Enter a real date as `Month/Day/Year`, such as `10/14/2026` or `10/14/26`.",
                view=LogDetailsRetryView(
                    interaction.user.id,
                    self.draft,
                    self.dependencies,
                    LogData(
                        review=str(self.review_input).strip() or None,
                        rating=selected_rating(self.rating_input),
                        activity_date=None,
                    ),
                    self.publish_input.values[0] == "yes",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await save_new_log(
            interaction,
            self.draft,
            log_data,
            self.dependencies,
            publish=self.publish_input.values[0] == "yes",
        )


class LogDetailsRetryView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        draft: MediaDraft,
        dependencies: LogDependencies,
        initial: LogData,
        publish: bool,
    ) -> None:
        super().__init__(owner_id)
        self.draft = draft
        self.dependencies = dependencies
        self.initial = initial
        self.publish = publish

    @discord.ui.button(label="Correct the date", style=discord.ButtonStyle.primary)
    async def retry(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LogDetailsRetryView],
    ) -> None:
        await interaction.response.send_modal(
            LogDetailsModal(
                self.draft,
                self.dependencies,
                initial=self.initial,
                publish=self.publish,
            )
        )


async def save_new_log(
    interaction: discord.Interaction,
    draft: MediaDraft,
    log_data: LogData,
    dependencies: LogDependencies,
    *,
    publish: bool,
) -> None:
    """Persist a validated log and optionally create its forum thread."""
    try:
        item = await persist_draft(dependencies, draft)
        media_log = await dependencies.repository.create_log(
            item.id,
            interaction.user.id,
            review=log_data.review,
            rating=log_data.rating,
            activity_date=log_data.activity_date,
            poster_path=draft.selected_poster_path,
        )
    except SQLAlchemyError as error:
        logger.exception("Could not persist media log")
        await interaction.followup.send(f"I couldn't save that log: {error}", ephemeral=True)
        return

    if not publish:
        await interaction.followup.send(
            f"Saved **{item.title}** privately as log `#{media_log.id}`.",
            ephemeral=True,
        )
        return

    try:
        thread, message = await dependencies.publisher.publish(
            interaction.client,
            interaction.user,
            media_log,
            item,
        )
        await dependencies.repository.set_forum_post(
            media_log.id,
            thread_id=thread.id,
            message_id=message.id,
        )
        await interaction.followup.send(
            f"Created {thread.mention} and saved log `#{media_log.id}`.",
            ephemeral=True,
        )
    except (discord.HTTPException, ForumConfigurationError, TmdbError) as error:
        logger.exception("Could not create forum post for saved media log")
        await interaction.followup.send(
            f"Saved private log `#{media_log.id}`, but couldn't create its forum post: {error}",
            ephemeral=True,
        )
    except SQLAlchemyError as error:
        logger.exception("Could not associate forum post with saved media log")
        await dependencies.publisher.delete_thread(interaction.client, thread.id)
        await interaction.followup.send(
            f"Saved private log `#{media_log.id}`, but couldn't register its forum post: {error}",
            ephemeral=True,
        )


class EditLogModal(discord.ui.Modal):
    """Edit only review, rating, and activity date on an existing log."""

    def __init__(
        self,
        media_log: MediaLog,
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(title="Edit your log", timeout=600)
        self.log_id = media_log.id
        self.dependencies = dependencies
        self.review_input: discord.ui.TextInput[EditLogModal] = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=media_log.review,
            placeholder="What did you think?",
            required=False,
            max_length=1800,
        )
        self.rating_input: discord.ui.Select[EditLogModal] = discord.ui.Select(
            placeholder="Select a rating",
            options=rating_options(media_log.rating),
            min_values=1,
            max_values=1,
            required=True,
        )
        self.date_input: discord.ui.TextInput[EditLogModal] = discord.ui.TextInput(
            default=(
                format_activity_date(media_log.activity_date) if media_log.activity_date else None
            ),
            placeholder="Month/Day/Year",
            required=False,
            max_length=10,
        )
        self.add_item(discord.ui.Label(text="Review (optional)", component=self.review_input))
        self.add_item(discord.ui.Label(text="Rating (optional)", component=self.rating_input))
        self.add_item(discord.ui.Label(text="Date (optional)", component=self.date_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            activity_date = parse_activity_date(str(self.date_input))
            rating = selected_rating(self.rating_input)
        except ValueError:
            await interaction.response.send_message(
                "Enter a real date as `Month/Day/Year`, such as `10/14/2026` or `10/14/26`. "
                "Please run `/edit` again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        owned = await self.dependencies.repository.get_owned_log(self.log_id, interaction.user.id)
        if owned is None:
            await interaction.followup.send("You no longer own that log.", ephemeral=True)
            return
        try:
            updated = await self.dependencies.repository.update_log_data(
                self.log_id,
                review=str(self.review_input).strip() or None,
                rating=rating,
                activity_date=activity_date,
            )
            await self.dependencies.publisher.update_log_message(
                interaction.client, interaction.user, updated
            )
            await interaction.followup.send("Your log was updated.", ephemeral=True)
        except (discord.HTTPException, ForumConfigurationError, SQLAlchemyError) as error:
            logger.exception("Could not update media log")
            await interaction.followup.send(f"I couldn't update that log: {error}", ephemeral=True)


class EditChoiceView(OwnedView):
    """Choose between shared media details and member-specific log data."""

    def __init__(
        self,
        owner_id: int,
        media_log: MediaLog,
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.media_log = media_log
        self.dependencies = dependencies

    @discord.ui.button(label="Your log", style=discord.ButtonStyle.primary)
    async def edit_log(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[EditChoiceView],
    ) -> None:
        await interaction.response.send_modal(EditLogModal(self.media_log, self.dependencies))

    @discord.ui.button(label="Media details", style=discord.ButtonStyle.secondary)
    async def edit_media(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[EditChoiceView],
    ) -> None:
        warning = (
            "Changing media details creates a new forum post. "
            "The current thread and all of its discussion will be permanently deleted."
            if self.media_log.forum_thread_id is not None
            else "Changing media details replaces the title and artwork for this private log."
        )
        await interaction.response.send_message(
            warning,
            view=MediaEditWarningView(
                interaction.user.id,
                self.media_log,
                self.dependencies,
            ),
            ephemeral=True,
        )


class MediaEditWarningView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        media_log: MediaLog,
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.media_log = media_log
        self.dependencies = dependencies

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.danger)
    async def continue_edit(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[MediaEditWarningView],
    ) -> None:
        await interaction.response.send_modal(
            ManualMediaModal(
                MediaDraft(
                    kind=self.media_log.media_item.media_type,
                    title=self.media_log.media_item.title,
                ),
                self.dependencies,
                replacement_log_id=self.media_log.id,
            )
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[MediaEditWarningView],
    ) -> None:
        await interaction.response.edit_message(content="No changes were made.", view=None)


async def replace_media_details(
    interaction: discord.Interaction,
    log_id: int,
    draft: MediaDraft,
    dependencies: LogDependencies,
) -> None:
    """Replace media details, recreating the forum thread when one exists."""
    media_log = await dependencies.repository.get_owned_log(log_id, interaction.user.id)
    if media_log is None:
        await interaction.followup.send("You no longer own that log.", ephemeral=True)
        return

    try:
        if draft.artwork_data is None:
            existing_art = await dependencies.publisher.artwork(media_log, media_log.media_item)
            if existing_art is not None:
                draft.artwork_data = existing_art.data
                draft.artwork_filename = existing_art.filename
                draft.artwork_content_type = (
                    media_log.media_item.artwork_content_type or "image/jpeg"
                )
        item = await persist_draft(dependencies, draft)
        if media_log.forum_thread_id is None:
            await dependencies.repository.replace_log_media(media_log.id, item.id)
            await interaction.followup.send("The media details were updated.", ephemeral=True)
            return

        old_thread_id = media_log.forum_thread_id
        thread, message = await dependencies.publisher.publish(
            interaction.client,
            interaction.user,
            media_log,
            item,
        )
        await dependencies.repository.replace_log_media(
            media_log.id,
            item.id,
            thread_id=thread.id,
            message_id=message.id,
        )
        await dependencies.publisher.delete_thread(interaction.client, old_thread_id)
        await interaction.followup.send(
            f"Created replacement post {thread.mention}. The old thread was deleted.",
            ephemeral=True,
        )
    except (discord.HTTPException, ForumConfigurationError, SQLAlchemyError, TmdbError) as error:
        logger.exception("Could not replace media details")
        await interaction.followup.send(
            f"I couldn't replace those details: {error}", ephemeral=True
        )


class DeleteConfirmationView(OwnedView):
    """Require explicit confirmation before deleting post and log history."""

    def __init__(
        self,
        owner_id: int,
        media_log: MediaLog,
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.media_log = media_log
        self.dependencies = dependencies

    @discord.ui.button(label="Delete permanently", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[DeleteConfirmationView],
    ) -> None:
        owned = await self.dependencies.repository.get_owned_log(
            self.media_log.id, interaction.user.id
        )
        if owned is None:
            await interaction.response.edit_message(
                content="That log no longer exists or is not yours.", view=None
            )
            return
        await interaction.response.edit_message(content="Deleting the post and log…", view=None)
        try:
            if owned.forum_thread_id is not None:
                await self.dependencies.publisher.delete_thread(
                    interaction.client, owned.forum_thread_id
                )
            await self.dependencies.repository.delete_log(owned.id)
            with suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content="The forum post and log history were deleted."
                )
        except (discord.HTTPException, SQLAlchemyError) as error:
            logger.exception("Could not delete media log")
            with suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=f"I couldn't finish deleting that log: {error}"
                )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[DeleteConfirmationView],
    ) -> None:
        await interaction.response.edit_message(content="Nothing was deleted.", view=None)


class LogsView(OwnedView):
    """Browse and manage a paginated history of all member-owned logs."""

    def __init__(
        self,
        owner_id: int,
        logs: list[MediaLog],
        dependencies: LogDependencies,
        *,
        page: int = 0,
        has_next: bool = False,
    ) -> None:
        super().__init__(owner_id)
        self.logs = {media_log.id: media_log for media_log in logs}
        self.dependencies = dependencies
        self.page = page
        self.has_next = has_next
        self.selected_log_id: int | None = None
        self.add_item(OwnedLogSelect(logs))
        self.previous_page.disabled = page == 0
        self.next_page.disabled = not has_next

    def selected(self) -> MediaLog | None:
        if self.selected_log_id is None:
            return None
        return self.logs.get(self.selected_log_id)

    @discord.ui.button(label="Edit your log", style=discord.ButtonStyle.primary)
    async def edit_log(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LogsView],
    ) -> None:
        media_log = self.selected()
        if media_log is None:
            await interaction.response.send_message("Choose a log first.", ephemeral=True)
            return
        await interaction.response.send_modal(EditLogModal(media_log, self.dependencies))

    @discord.ui.button(label="Edit media", style=discord.ButtonStyle.secondary)
    async def edit_media(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LogsView],
    ) -> None:
        media_log = self.selected()
        if media_log is None:
            await interaction.response.send_message("Choose a log first.", ephemeral=True)
            return
        await interaction.response.send_message(
            "This replaces the title and optional artwork for the selected log.",
            view=MediaEditWarningView(
                interaction.user.id,
                media_log,
                self.dependencies,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_log(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LogsView],
    ) -> None:
        media_log = self.selected()
        if media_log is None:
            await interaction.response.send_message("Choose a log first.", ephemeral=True)
            return
        await interaction.response.send_message(
            "This permanently removes the item from your log history. Continue?",
            view=DeleteConfirmationView(
                interaction.user.id,
                media_log,
                self.dependencies,
            ),
            ephemeral=True,
        )

    async def show_page(self, interaction: discord.Interaction, page: int) -> None:
        results = await self.dependencies.repository.list_owned_logs(
            self.owner_id,
            offset=page * LOGS_PAGE_SIZE,
            limit=LOGS_PAGE_SIZE + 1,
        )
        page_logs = results[:LOGS_PAGE_SIZE]
        if not page_logs:
            await interaction.response.send_message(
                "There are no logs on that page.", ephemeral=True
            )
            return

        current_select = next(
            (item for item in self.children if isinstance(item, OwnedLogSelect)),
            None,
        )
        if current_select is not None:
            self.remove_item(current_select)
        self.logs = {media_log.id: media_log for media_log in page_logs}
        self.page = page
        self.has_next = len(results) > LOGS_PAGE_SIZE
        self.selected_log_id = None
        self.previous_page.disabled = page == 0
        self.next_page.disabled = not self.has_next
        self.add_item(OwnedLogSelect(page_logs))
        await interaction.response.edit_message(
            content=f"Choose one of your logs — page {page + 1}:",
            view=self,
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LogsView],
    ) -> None:
        await self.show_page(interaction, max(0, self.page - 1))

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LogsView],
    ) -> None:
        await self.show_page(interaction, self.page + 1)


class OwnedLogSelect(discord.ui.Select[LogsView]):
    def __init__(self, logs: list[MediaLog]) -> None:
        options = [
            discord.SelectOption(
                label=media_log.media_item.title[:100],
                value=str(media_log.id),
                description=(
                    f"{MEDIA_TYPES[media_log.media_item.media_type].label} • "
                    f"{'Forum post' if media_log.forum_thread_id else 'Private'} • "
                    f"Log #{media_log.id}"
                )[:100],
            )
            for media_log in logs
        ]
        super().__init__(placeholder="Choose one of your logs", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:
            return
        self.view.selected_log_id = int(self.values[0])
        media_log = self.view.selected()
        if media_log is None:
            return
        post_link = f" • <#{media_log.forum_thread_id}>" if media_log.forum_thread_id else ""
        await interaction.response.edit_message(
            content=f"Selected **{media_log.media_item.title}** (`#{media_log.id}`){post_link}.",
            view=self.view,
        )
