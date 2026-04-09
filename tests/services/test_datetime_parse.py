from datetime import date, datetime, time, timedelta, timezone

from bot.services.datetime_parse import (
    combine_local,
    parse_date_ru,
    parse_duration_ru,
    parse_relative_ru,
    parse_time_ru,
)


def test_parse_date_relative_keywords() -> None:
    today = date(2026, 3, 25)
    assert parse_date_ru("сегодня", today).target_date == date(2026, 3, 25)
    assert parse_date_ru("завтра", today).target_date == date(2026, 3, 26)
    assert parse_date_ru("послезавтра", today).target_date == date(2026, 3, 27)


def test_parse_date_weekday_rolls_to_next_week_for_same_day() -> None:
    # 2026-03-23 is Monday; "понедельник" should mean next Monday.
    today = date(2026, 3, 23)
    assert parse_date_ru("понедельник", today).target_date == date(2026, 3, 30)


def test_parse_date_day_month_rolls_to_next_year_when_past() -> None:
    today = date(2026, 12, 30)
    res = parse_date_ru("01.01", today)
    assert res is not None
    assert res.target_date == date(2027, 1, 1)
    assert res.normalized == "01.01.2027"


def test_parse_date_invalid_returns_none() -> None:
    assert parse_date_ru("31.02.2026", date(2026, 3, 25)) is None


def test_parse_time_formats_and_parts_of_day() -> None:
    assert parse_time_ru("16:40") == time(16, 40)
    assert parse_time_ru("16 40") == time(16, 40)
    assert parse_time_ru("5 вечера") == time(17, 0)
    assert parse_time_ru("12 ночи") == time(0, 0)
    assert parse_time_ru("7") == time(7, 0)


def test_parse_time_invalid_returns_none() -> None:
    assert parse_time_ru("24:00") is None
    assert parse_time_ru("абракадабра") is None


def test_parse_relative_ru() -> None:
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    assert parse_relative_ru("через час", now) == now + timedelta(hours=1)
    assert parse_relative_ru("через 1 час 7 минут", now) == now + timedelta(hours=1, minutes=7)
    assert parse_relative_ru("через скоро", now) is None


def test_parse_duration_ru() -> None:
    assert parse_duration_ru("1ч30м") == timedelta(hours=1, minutes=30)
    assert parse_duration_ru("2 часа 5 минут") == timedelta(hours=2, minutes=5)
    assert parse_duration_ru("15") == timedelta(minutes=15)
    assert parse_duration_ru("непонятно") is None


def test_combine_local_preserves_timezone_info() -> None:
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    combined = combine_local(date(2026, 4, 1), time(9, 15), now)
    assert combined == datetime(2026, 4, 1, 9, 15, tzinfo=timezone.utc)
