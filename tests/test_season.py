from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from media_club.season import (
    Season,
    SeasonClosedError,
    active_season,
    default_activity_date,
    season_is_editable,
    validate_activity_date,
    validate_new_log_date,
)

CHICAGO = ZoneInfo("America/Chicago")


def local_time(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=CHICAGO)


def test_logging_window_uses_the_four_am_server_day() -> None:
    assert active_season(local_time(2026, 9, 28, 3)) is None
    assert active_season(local_time(2026, 9, 28, 4)) == Season(2026)
    assert active_season(local_time(2026, 11, 8, 3)) == Season(2026)
    assert active_season(local_time(2026, 11, 8, 4)) is None


def test_november_grace_period_defaults_to_october_31() -> None:
    assert default_activity_date(local_time(2026, 11, 5)) == date(2026, 10, 31)


def test_activity_date_must_be_in_the_current_viewing_period() -> None:
    now = local_time(2026, 10, 20)
    assert validate_new_log_date(date(2026, 9, 28), now) == 2026
    assert validate_new_log_date(date(2026, 10, 20), now) == 2026
    with pytest.raises(ValueError, match="cannot be in the future"):
        validate_new_log_date(date(2026, 10, 21), now)
    with pytest.raises(ValueError, match="between 9/28/2026 and 10/31/2026"):
        validate_new_log_date(date(2026, 11, 1), local_time(2026, 11, 5))


def test_undated_grace_period_log_still_gets_the_season_year() -> None:
    assert validate_new_log_date(None, local_time(2026, 11, 7)) == 2026


def test_closed_seasons_are_read_only() -> None:
    assert season_is_editable(2026, local_time(2026, 10, 20))
    assert not season_is_editable(2025, local_time(2026, 10, 20))
    assert not season_is_editable(2026, local_time(2026, 11, 8, 4))
    with pytest.raises(SeasonClosedError):
        validate_new_log_date(None, local_time(2026, 11, 8, 4))


def test_blank_dates_remain_optional() -> None:
    validate_activity_date(None, Season(2026), local_time(2026, 10, 20))
