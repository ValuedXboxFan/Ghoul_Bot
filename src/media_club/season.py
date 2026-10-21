from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ACTIVITY_TIMEZONE = ZoneInfo("America/Chicago")


@dataclass(frozen=True, slots=True)
class Season:
    """Calendar boundaries for one immutable Ghoulstober season."""

    year: int

    @property
    def logging_opens(self) -> date:
        return date(self.year, 9, 28)

    @property
    def activity_closes(self) -> date:
        return date(self.year, 10, 31)

    @property
    def logging_closes(self) -> date:
        return date(self.year, 11, 7)


class SeasonClosedError(ValueError):
    """Raised when a member attempts to modify a closed season."""


def activity_day(now: datetime | None = None) -> date:
    """Return the server day using its established 4 AM Chicago boundary."""
    local_now = now.astimezone(ACTIVITY_TIMEZONE) if now else datetime.now(ACTIVITY_TIMEZONE)
    return (local_now - timedelta(hours=4)).date()


def active_season(now: datetime | None = None) -> Season | None:
    """Return the season currently accepting submissions, if any."""
    today = activity_day(now)
    season = Season(today.year)
    if season.logging_opens <= today <= season.logging_closes:
        return season
    return None


def default_activity_date(now: datetime | None = None) -> date:
    """Return a valid default date, clamping the November grace period to October 31."""
    today = activity_day(now)
    season = active_season(now)
    if season is not None:
        return min(max(today, season.logging_opens), season.activity_closes)
    return today


def validate_new_log_date(value: date | None, now: datetime | None = None) -> int:
    """Validate a new entry and return the active season year."""
    season = active_season(now)
    if season is None:
        raise SeasonClosedError("Logging is open from September 28 through November 7 each year.")
    validate_activity_date(value, season, now)
    return season.year


def validate_activity_date(
    value: date | None,
    season: Season,
    now: datetime | None = None,
) -> None:
    """Require supplied activity dates to belong to the active viewing period."""
    if value is None:
        return
    if not season.logging_opens <= value <= season.activity_closes:
        raise ValueError(f"Date must be between 9/28/{season.year} and 10/31/{season.year}.")
    if value > activity_day(now):
        raise ValueError("The activity date cannot be in the future.")


def season_is_editable(season_year: int, now: datetime | None = None) -> bool:
    """Return whether the referenced season is currently accepting changes."""
    season = active_season(now)
    return season is not None and season.year == season_year


def closed_season_message(season_year: int) -> str:
    """Explain why an archived season cannot be modified."""
    return (
        f"The {season_year} season is closed. Previous seasons can be viewed, "
        "but their logs cannot be added, edited, or deleted."
    )
