from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pytest

from ninja_bot.activity.periods import (
    FUTURE_MONTH_MESSAGE,
    INVALID_MONTH_MESSAGE,
    MIN_YEAR,
    InvalidMonthError,
    Month,
    month_of,
    parse_month,
    resolve_month,
    split_local,
)

UTC_TZ = ZoneInfo("UTC")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def test_key_is_zero_padded() -> None:
    assert Month(2026, 9).key == "2026-09"
    assert Month(2026, 12).key == "2026-12"


def test_start_and_end_in_utc() -> None:
    month = Month(2026, 9)
    assert month.start(UTC_TZ) == datetime(2026, 9, 1, tzinfo=UTC)
    assert month.end(UTC_TZ) == datetime(2026, 10, 1, tzinfo=UTC)


def test_year_rollover() -> None:
    december = Month(2026, 12)
    assert december.next() == Month(2027, 1)
    assert Month(2027, 1).previous() == december
    assert december.end(UTC_TZ) == datetime(2027, 1, 1, tzinfo=UTC)


def test_previous_and_next_within_year() -> None:
    assert Month(2026, 5).previous() == Month(2026, 4)
    assert Month(2026, 5).next() == Month(2026, 6)


@pytest.mark.parametrize(
    ("month", "days"),
    [
        (Month(2024, 2), 29),
        (Month(2026, 2), 28),
        (Month(2000, 2), 29),
        (Month(1900, 2), 28),
        (Month(2026, 4), 30),
        (Month(2026, 12), 31),
    ],
)
def test_days(month: Month, days: int) -> None:
    assert month.days() == days


def test_march_in_amsterdam_crosses_dst_start() -> None:
    month = Month(2026, 3)
    # CET (UTC+1) at the start, CEST (UTC+2) at the end (DST starts 29 March).
    assert month.start(AMSTERDAM) == datetime(2026, 2, 28, 23, tzinfo=UTC)
    assert month.end(AMSTERDAM) == datetime(2026, 3, 31, 22, tzinfo=UTC)
    length = month.end(AMSTERDAM).timestamp() - month.start(AMSTERDAM).timestamp()
    assert length == timedelta(days=31, hours=-1).total_seconds()


def test_october_in_amsterdam_crosses_dst_end() -> None:
    month = Month(2026, 10)
    # CEST at the start, CET at the end (DST ends 25 October).
    assert month.start(AMSTERDAM) == datetime(2026, 9, 30, 22, tzinfo=UTC)
    assert month.end(AMSTERDAM) == datetime(2026, 10, 31, 23, tzinfo=UTC)
    length = month.end(AMSTERDAM).timestamp() - month.start(AMSTERDAM).timestamp()
    assert length == timedelta(days=31, hours=1).total_seconds()


def test_month_of_uses_local_time() -> None:
    instant = datetime(2026, 9, 30, 22, 30, tzinfo=UTC).timestamp()
    assert month_of(instant, UTC_TZ) == Month(2026, 9)
    assert month_of(instant, AMSTERDAM) == Month(2026, 10)


def test_month_of_boundaries_are_start_inclusive() -> None:
    month = Month(2026, 3)
    assert month_of(month.start(AMSTERDAM).timestamp(), AMSTERDAM) == month
    assert month_of(month.end(AMSTERDAM).timestamp(), AMSTERDAM) == month.next()
    last = month.end(AMSTERDAM).timestamp() - 0.001
    assert month_of(last, AMSTERDAM) == month


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09", Month(2026, 9)),
        ("2026-01", Month(2026, 1)),
        ("2026-12", Month(2026, 12)),
        (" 2025-02 ", Month(2025, 2)),
    ],
)
def test_parse_month(text: str, expected: Month) -> None:
    assert parse_month(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "2026",
        "2026-9",
        "2026/09",
        "09-2026",
        "2026-00",
        "2026-13",
        "2026-09-01",
        "september",
        "２０２６-０９",
        "0000-01",
        "0001-01",
        "2014-12",
        "9999-12",
        "2015-13",
    ],
)
def test_parse_month_rejects_invalid_text(text: str) -> None:
    with pytest.raises(InvalidMonthError) as excinfo:
        parse_month(text)
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.message == INVALID_MONTH_MESSAGE
    assert str(excinfo.value) == excinfo.value.message


@pytest.mark.parametrize("month", [0, 13])
def test_month_constructor_rejects_out_of_range(month: int) -> None:
    with pytest.raises(InvalidMonthError):
        Month(2026, month)


def test_resolve_month_defaults_to_the_current_local_month() -> None:
    now = datetime(2026, 10, 31, 23, 30, tzinfo=UTC).timestamp()
    assert resolve_month(None, ZoneInfo("UTC"), now) == Month(2026, 10)
    assert resolve_month("  ", ZoneInfo("Europe/Amsterdam"), now) == Month(2026, 11)
    assert resolve_month("2026-10", ZoneInfo("UTC"), now) == Month(2026, 10)
    assert resolve_month("2020-01", ZoneInfo("UTC"), now) == Month(2020, 1)


def test_resolve_month_rejects_future_and_bad_months() -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC).timestamp()
    with pytest.raises(InvalidMonthError) as future:
        resolve_month("2026-11", ZoneInfo("UTC"), now)
    assert future.value.message == FUTURE_MONTH_MESSAGE
    with pytest.raises(InvalidMonthError) as bad:
        resolve_month("Oct", ZoneInfo("UTC"), now)
    assert bad.value.message == INVALID_MONTH_MESSAGE


@pytest.mark.parametrize(
    "tz", [UTC_TZ, AMSTERDAM, ZoneInfo("Pacific/Kiritimati"), ZoneInfo("Pacific/Pago_Pago")]
)
def test_earliest_accepted_month_has_a_safe_previous_month(tz: ZoneInfo) -> None:
    month = parse_month(f"{MIN_YEAR}-01")
    assert month == Month(MIN_YEAR, 1)
    previous = month.previous()
    assert previous == Month(MIN_YEAR - 1, 12)
    assert previous.start(tz).timestamp() < month.start(tz).timestamp()
    assert month.end(tz).timestamp() > month.start(tz).timestamp()


def test_resolve_month_rejects_months_before_min_year() -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC).timestamp()
    for text in ("0001-01", "2014-12"):
        with pytest.raises(InvalidMonthError) as excinfo:
            resolve_month(text, UTC_TZ, now)
        assert excinfo.value.message == INVALID_MONTH_MESSAGE
    assert resolve_month("2015-01", UTC_TZ, now) == Month(2015, 1)


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=AMSTERDAM).timestamp()


def _pieces(start: float, end: float, unit: Literal["hour", "day"]) -> list[tuple[float, float]]:
    return [
        (piece_start, piece_end)
        for piece_start, piece_end, _ in split_local(start, end, AMSTERDAM, unit)
    ]


def test_split_local_within_one_hour_is_one_piece() -> None:
    start, end = _ts(2026, 9, 3, 14, 10), _ts(2026, 9, 3, 14, 50)
    assert list(split_local(start, end, AMSTERDAM, "hour")) == [
        (start, end, datetime(2026, 9, 3, 14, 10, tzinfo=AMSTERDAM))
    ]
    assert _pieces(start, end, "day") == [(start, end)]


def test_split_local_spanning_midnight() -> None:
    start, end = _ts(2026, 9, 3, 23, 30), _ts(2026, 9, 4, 0, 45)
    midnight = _ts(2026, 9, 4)
    assert _pieces(start, end, "day") == [(start, midnight), (midnight, end)]
    assert _pieces(start, end, "hour") == [(start, midnight), (midnight, end)]
    days = [local.day for _, _, local in split_local(start, end, AMSTERDAM, "day")]
    assert days == [3, 4]


def test_split_local_spanning_several_days() -> None:
    start, end = _ts(2026, 9, 3, 12), _ts(2026, 9, 6, 6)
    pieces = list(split_local(start, end, AMSTERDAM, "day"))
    assert [local.day for _, _, local in pieces] == [3, 4, 5, 6]
    assert [piece_end - piece_start for piece_start, piece_end, _ in pieces] == [
        12 * 3600,
        24 * 3600,
        24 * 3600,
        6 * 3600,
    ]
    hours = _pieces(start, end, "hour")
    assert len(hours) == 66
    assert hours[0][0] == start and hours[-1][1] == end


def test_split_local_spring_forward_day() -> None:
    start, end = _ts(2026, 3, 29), _ts(2026, 3, 30)
    days = list(split_local(start, end, AMSTERDAM, "day"))
    assert [(piece_end - piece_start, local.day) for piece_start, piece_end, local in days] == [
        (23 * 3600, 29)
    ]
    hours = list(split_local(start, end, AMSTERDAM, "hour"))
    assert len(hours) == 23
    assert all(piece_end - piece_start == 3600 for piece_start, piece_end, _ in hours)
    assert 2 not in [local.hour for _, _, local in hours]
    assert sum(piece_end - piece_start for piece_start, piece_end, _ in hours) == end - start


def test_split_local_fall_back_day() -> None:
    start, end = _ts(2026, 10, 25), _ts(2026, 10, 26)
    days = list(split_local(start, end, AMSTERDAM, "day"))
    assert [(piece_end - piece_start, local.day) for piece_start, piece_end, local in days] == [
        (25 * 3600, 25)
    ]
    hours = list(split_local(start, end, AMSTERDAM, "hour"))
    assert sum(piece_end - piece_start for piece_start, piece_end, _ in hours) == 25 * 3600
    # The repeated 02:00 hour stays one two-hour piece: both count as local hour 2.
    two_am = [piece_end - piece_start for piece_start, piece_end, local in hours if local.hour == 2]
    assert two_am == [2 * 3600]
    assert len(hours) == 24


def test_split_local_zero_length_interval_yields_nothing() -> None:
    point = _ts(2026, 9, 3, 12)
    assert list(split_local(point, point, AMSTERDAM, "hour")) == []
    assert list(split_local(point, point - 60, AMSTERDAM, "day")) == []
