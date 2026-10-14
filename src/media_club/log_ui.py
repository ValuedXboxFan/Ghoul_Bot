from __future__ import annotations

import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

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
from media_club.tmdb import TmdbClient, TmdbError, TmdbMovie

logger = logging.getLogger(__name__)

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RATINGS = tuple(Decimal(value) / 2 for value in range(2, 11))


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
                label=f"{rating.normalize()} / 5",
                value=str(rating),
                default=rating == selected,
            )
            for rating in RATINGS
        ],
    ]


def parse_activity_date(value: str) -> date | None:
    """Parse a strict ISO date while preserving an intentionally blank value."""
    clean = value.strip()
    if not clean:
        return None
    if DATE_PATTERN.fullmatch(clean) is None:
        raise ValueError("Use YYYY-MM-DD format")
    return date.fromisoformat(clean)


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


class MovieLookupView(OwnedView):
    """Choose a TMDB movie or switch to manual entry."""

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
        if self.dependencies.tmdb is None:
            await interaction.followup.send(
                "Movie lookup is unavailable. You can still enter it manually.",
                view=ManualStartView(
                    interaction.user.id,
                    MediaDraft(kind="movie", title=query),
                    self.dependencies,
                ),
                ephemeral=True,
            )
            return
        try:
            movies = await self.dependencies.tmdb.search_movies(query)
        except TmdbError:
            logger.exception("TMDB lookup failed during movie log")
            movies = []
        if not movies:
            await interaction.followup.send(
                "No useful movie results were found. You can enter it manually.",
                view=ManualStartView(
                    interaction.user.id,
                    MediaDraft(kind="movie", title=query),
                    self.dependencies,
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "Choose the correct movie or switch to manual entry:",
            view=MovieLookupView(
                interaction.user.id,
                query,
                movies,
                self.dependencies,
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
        await interaction.response.send_modal(ManualMediaModal(self.draft, self.dependencies))


class MovieLookupSelect(discord.ui.Select[MovieLookupView]):
    """Movie result picker with an explicit manual fallback."""

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
            for movie in movies[:24]
        ]
        options.append(
            discord.SelectOption(
                label="Enter manually",
                value="manual",
                description="Use your own title and optional artwork",
                emoji="✏️",
            )
        )
        super().__init__(
            placeholder="Choose the correct movie or enter it manually",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "manual":
            await interaction.response.send_modal(
                ManualMediaModal(
                    MediaDraft(kind="movie", title=self.query),
                    self.dependencies,
                )
            )
        else:
            movie = self.movies[self.values[0]]
            await interaction.response.send_modal(
                LogDetailsModal(
                    MediaDraft(kind="movie", title=movie.title, tmdb_movie=movie),
                    self.dependencies,
                )
            )
        if interaction.message is not None:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                logger.warning("Could not remove completed movie picker", exc_info=True)


class ManualMediaModal(discord.ui.Modal):
    """Collect manual title and optional artwork for new or replacement media."""

    def __init__(
        self,
        draft: MediaDraft,
        dependencies: LogDependencies,
        *,
        replacement_log_id: int | None = None,
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

        if self.replacement_log_id is None:
            await interaction.response.send_message(
                f"Media details ready for **{self.draft.title}**.",
                view=ContinueLogView(interaction.user.id, self.draft, self.dependencies),
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
        replacement_log_id: int | None,
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


class ContinueLogView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        draft: MediaDraft,
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.draft = draft
        self.dependencies = dependencies

    @discord.ui.button(label="Add your log details", style=discord.ButtonStyle.primary)
    async def continue_log(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[ContinueLogView],
    ) -> None:
        await interaction.response.send_modal(LogDetailsModal(self.draft, self.dependencies))


class LogDetailsModal(discord.ui.Modal):
    """Collect optional review, constrained rating, date, and post preference."""

    def __init__(
        self,
        draft: MediaDraft,
        dependencies: LogDependencies,
        *,
        initial: LogData | None = None,
        skip_forum: bool = False,
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
        self.date_input: discord.ui.TextInput[LogDetailsModal] = discord.ui.TextInput(
            default=(
                initial.activity_date.isoformat()
                if initial is not None and initial.activity_date is not None
                else None
            ),
            placeholder="YYYY-MM-DD",
            required=False,
            min_length=10,
            max_length=10,
        )
        self.skip_input: discord.ui.Checkbox[LogDetailsModal] = discord.ui.Checkbox(
            default=skip_forum
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
                description="Leave blank to record no activity date",
                component=self.date_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Skip the forum post",
                description="Save this only in your private log history",
                component=self.skip_input,
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
                "The date must be a real date in `YYYY-MM-DD` format.",
                view=LogDetailsRetryView(
                    interaction.user.id,
                    self.draft,
                    self.dependencies,
                    LogData(
                        review=str(self.review_input).strip() or None,
                        rating=selected_rating(self.rating_input),
                        activity_date=None,
                    ),
                    self.skip_input.value,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            item = await persist_draft(self.dependencies, self.draft)
            media_log = await self.dependencies.repository.create_log(
                item.id,
                interaction.user.id,
                review=log_data.review,
                rating=log_data.rating,
                activity_date=log_data.activity_date,
            )
        except SQLAlchemyError as error:
            logger.exception("Could not persist media log")
            await interaction.followup.send(f"I couldn't save that log: {error}", ephemeral=True)
            return

        if self.skip_input.value:
            await interaction.followup.send(
                f"Saved **{item.title}** privately as log `#{media_log.id}`.",
                ephemeral=True,
            )
            return

        try:
            thread, message = await self.dependencies.publisher.publish(
                interaction.client,
                interaction.user,
                media_log,
                item,
            )
            await self.dependencies.repository.set_forum_post(
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
            await self.dependencies.publisher.delete_thread(interaction.client, thread.id)
            await interaction.followup.send(
                f"Saved private log `#{media_log.id}`, but couldn't register its forum post: "
                f"{error}",
                ephemeral=True,
            )


class LogDetailsRetryView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        draft: MediaDraft,
        dependencies: LogDependencies,
        initial: LogData,
        skip_forum: bool,
    ) -> None:
        super().__init__(owner_id)
        self.draft = draft
        self.dependencies = dependencies
        self.initial = initial
        self.skip_forum = skip_forum

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
                skip_forum=self.skip_forum,
            )
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
            default=media_log.activity_date.isoformat() if media_log.activity_date else None,
            placeholder="YYYY-MM-DD",
            required=False,
            min_length=10,
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
                "The date must be a real date in `YYYY-MM-DD` format. Please run `/edit` again.",
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
            existing_art = await dependencies.publisher.artwork(media_log.media_item)
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
    """Manage private logs that do not have a forum post."""

    def __init__(
        self,
        owner_id: int,
        logs: list[MediaLog],
        dependencies: LogDependencies,
    ) -> None:
        super().__init__(owner_id)
        self.logs = {media_log.id: media_log for media_log in logs}
        self.dependencies = dependencies
        self.selected_log_id: int | None = None
        self.add_item(PrivateLogSelect(logs))

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
            "This replaces the title and optional artwork for the selected private log.",
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


class PrivateLogSelect(discord.ui.Select[LogsView]):
    def __init__(self, logs: list[MediaLog]) -> None:
        options = [
            discord.SelectOption(
                label=media_log.media_item.title[:100],
                value=str(media_log.id),
                description=(
                    f"{MEDIA_TYPES[media_log.media_item.media_type].label} • Log #{media_log.id}"
                )[:100],
            )
            for media_log in logs
        ]
        super().__init__(placeholder="Choose a private log", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:
            return
        self.view.selected_log_id = int(self.values[0])
        media_log = self.view.selected()
        if media_log is None:
            return
        await interaction.response.edit_message(
            content=f"Selected **{media_log.media_item.title}** (`#{media_log.id}`).",
            view=self.view,
        )
