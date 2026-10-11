from __future__ import annotations

import ast
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ninja_bot.sessions import slots
from ninja_bot.sessions.slots import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_WEEKS,
    INVALID_DAYS_MESSAGE,
    INVALID_START_DATE_MESSAGE,
    INVALID_TIMES_MESSAGE,
    MAX_DAYS_LENGTH,
    MAX_SLOTS,
    MAX_TIMES_LENGTH,
    MAX_WEEKS,
    NO_SLOTS_MESSAGE,
    PAST_START_DATE_MESSAGE,
    InvalidSessionInput,
    Slot,
    generate_slots,
    next_window_start,
    parse_days,
    parse_start_date,
    parse_times,
)


def test_slots_does_not_import_discord() -> None:
    tree = ast.parse(Path(slots.__file__).read_text(encoding="utf-8"))
    modules = [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    ]
    assert not any(module.split(".")[0] == "discord" for module in modules)


def test_invalid_session_input_is_value_error_with_message() -> None:
    error = InvalidSessionInput("nope")
    assert isinstance(error, ValueError)
    assert error.message == "nope"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mon", (0,)),
        ("tue", (1,)),
        ("wed", (2,)),
        ("thu", (3,)),
        ("fri", (4,)),
        ("sat", (5,)),
        ("sun", (6,)),
        ("monday", (0,)),
        ("tuesday", (1,)),
        ("wednesday", (2,)),
        ("thursday", (3,)),
        ("friday", (4,)),
        ("saturday", (5,)),
        ("sunday", (6,)),
        ("fri,sat", (4, 5)),
        ("Friday, SATURDAY", (4, 5)),
        ("  fri ,  sat  ", (4, 5)),
        ("weekend", (5, 6)),
        ("weekdays", (0, 1, 2, 3, 4)),
        ("WeekEnd", (5, 6)),
        ("sun,fri,mon", (0, 4, 6)),
        ("fri,friday,FRI", (4,)),
        ("weekend,sat,fri", (4, 5, 6)),
        ("weekdays,weekend", (0, 1, 2, 3, 4, 5, 6)),
    ],
)
def test_parse_days_accepts(text: str, expected: tuple[int, ...]) -> None:
    assert parse_days(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        ",",
        "fri,,sat",
        "fri,",
        "funday",
        "fr",
        "frid",
        "weekday",
        "1",
        "fri sat",
        "fri;sat",
    ],
)
def test_parse_days_rejects(text: str) -> None:
    with pytest.raises(InvalidSessionInput) as info:
        parse_days(text)
    assert info.value.message == INVALID_DAYS_MESSAGE


def test_parse_days_length_cap() -> None:
    padded = "fri" + " " * (MAX_DAYS_LENGTH - 3)
    assert parse_days(padded) == (4,)
    with pytest.raises(InvalidSessionInput):
        parse_days(padded + " ")


def test_parse_days_length_checked_before_splitting() -> None:
    class Text(str):
        def split(self, *args: object, **kwargs: object) -> list[str]:  # type: ignore[override]
            raise AssertionError("split before the length check")

    with pytest.raises(InvalidSessionInput):
        parse_days(Text("fri," * 100))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9:00", (time(9, 0),)),
        ("09:00", (time(9, 0),)),
        ("0:00", (time(0, 0),)),
        ("00:00", (time(0, 0),)),
        ("19:30", (time(19, 30),)),
        ("23:59", (time(23, 59),)),
        ("19:00,14:00", (time(14, 0), time(19, 0))),
        (" 19:00 ,  9:30 ", (time(9, 30), time(19, 0))),
        ("19:00,19:00,9:00,09:00", (time(9, 0), time(19, 0))),
    ],
)
def test_parse_times_accepts(text: str, expected: tuple[time, ...]) -> None:
    result = parse_times(text)
    assert result == expected
    assert all(value.tzinfo is None for value in result)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        ",",
        "19:00,",
        "19:00,,20:00",
        "24:00",
        "19:60",
        "7pm",
        "19",
        "19:0",
        "19:000",
        "019:00",
        "19.00",
        "19h00",
        "-1:00",
        "19:00 20:00",
        "١٩:٠٠",  # Arabic-Indic digits
        "１９:００",  # fullwidth digits
    ],
)
def test_parse_times_rejects(text: str) -> None:
    with pytest.raises(InvalidSessionInput) as info:
        parse_times(text)
    assert info.value.message == INVALID_TIMES_MESSAGE


def test_parse_times_length_cap() -> None:
    padded = "19:00" + " " * (MAX_TIMES_LENGTH - 5)
    assert parse_times(padded) == (time(19, 0),)
    with pytest.raises(InvalidSessionInput):
        parse_times(padded + " ")
    with pytest.raises(InvalidSessionInput):
        parse_times("9" * 10_000 + ":00")


AMS = ZoneInfo("Europe/Amsterdam")
FRI, SAT, SUN = 4, 5, 6
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=AMS).timestamp()


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def test_slot_constants() -> None:
    assert (MAX_SLOTS, MAX_WEEKS, DEFAULT_WEEKS, DEFAULT_DURATION_HOURS) == (25, 8, 2, 4)


def test_parse_start_date_blank_is_tomorrow_in_tz() -> None:
    # 23:30 UTC on Oct 10 is already Oct 11 in Amsterdam.
    now = _utc(2026, 10, 10, 23, 30)
    assert parse_start_date("", AMS, now) == date(2026, 10, 12)
    assert parse_start_date("  ", AMS, now) == date(2026, 10, 12)
    assert parse_start_date("", ZoneInfo("UTC"), now) == date(2026, 10, 11)


def test_parse_start_date_accepts_today_and_future() -> None:
    now = _ts(2026, 10, 11, 12)
    assert parse_start_date("2026-10-11", AMS, now) == date(2026, 10, 11)
    assert parse_start_date(" 2026-11-06 ", AMS, now) == date(2026, 11, 6)


def test_parse_start_date_rejects_past() -> None:
    with pytest.raises(InvalidSessionInput) as error:
        parse_start_date("2026-10-10", AMS, _ts(2026, 10, 11, 0, 5))
    assert error.value.message == PAST_START_DATE_MESSAGE


@pytest.mark.parametrize(
    "text",
    [
        "2026/11/06",
        "06-11-2026",
        "2026-13-01",
        "2026-02-30",
        "2026-1-6",
        "tomorrow",
        "２０２６-11-06",
        "2026-11-06T10:00",
    ],
)
def test_parse_start_date_rejects_bad_format(text: str) -> None:
    with pytest.raises(InvalidSessionInput) as error:
        parse_start_date(text, AMS, _ts(2026, 10, 11))
    assert error.value.message == INVALID_START_DATE_MESSAGE


def test_next_window_start() -> None:
    assert next_window_start(date(2026, 10, 12), 2) == date(2026, 10, 26)
    assert next_window_start(date(2026, 12, 28), 1) == date(2027, 1, 4)


def test_generate_slots_chronological_with_duration() -> None:
    # 2026-10-12 is a Monday; window is two weeks.
    result = generate_slots(
        date(2026, 10, 12), 2, (FRI, SAT), (time(19), time(14)), AMS, 4, _ts(2026, 10, 11)
    )
    assert [r.start for r in result] == [
        _ts(2026, 10, 16, 14),
        _ts(2026, 10, 16, 19),
        _ts(2026, 10, 17, 14),
        _ts(2026, 10, 17, 19),
        _ts(2026, 10, 23, 14),
        _ts(2026, 10, 23, 19),
        _ts(2026, 10, 24, 14),
        _ts(2026, 10, 24, 19),
    ]
    assert all(r.end == r.start + 4 * 3600 for r in result)
    assert result[0] == Slot(_ts(2026, 10, 16, 14), _ts(2026, 10, 16, 18))


def test_generate_slots_window_is_half_open() -> None:
    # Starting on a Friday: the Friday two weeks later is outside the window.
    result = generate_slots(date(2026, 10, 16), 2, (FRI,), (time(19),), AMS, 4, _ts(2026, 10, 11))
    assert [r.start for r in result] == [_ts(2026, 10, 16, 19), _ts(2026, 10, 23, 19)]


def test_generate_slots_crosses_month_boundary() -> None:
    result = generate_slots(date(2026, 10, 26), 2, (SAT,), (time(20),), AMS, 4, _ts(2026, 10, 11))
    assert [r.start for r in result] == [_ts(2026, 10, 31, 20), _ts(2026, 11, 7, 20)]


def test_generate_slots_drops_slots_not_after_now() -> None:
    now = _ts(2026, 10, 16, 14)
    result = generate_slots(
        date(2026, 10, 16), 1, (FRI,), (time(10), time(14), time(19)), AMS, 4, now
    )
    assert [r.start for r in result] == [_ts(2026, 10, 16, 19)]


def test_generate_slots_empty_raises() -> None:
    with pytest.raises(InvalidSessionInput) as error:
        generate_slots(date(2026, 10, 16), 1, (FRI,), (time(10),), AMS, 4, _ts(2026, 10, 17))
    assert error.value.message == NO_SLOTS_MESSAGE
    assert "no slots in that window" in error.value.message


def test_generate_slots_exactly_max_allowed() -> None:
    # 5 weeks x 5 weekdays x 1 time = 25 slots.
    result = generate_slots(
        date(2026, 10, 12), 5, (0, 1, 2, 3, 4), (time(19),), AMS, 4, _ts(2026, 10, 11)
    )
    assert len(result) == MAX_SLOTS


def test_generate_slots_over_max_rejected() -> None:
    with pytest.raises(InvalidSessionInput) as error:
        generate_slots(
            date(2026, 10, 12), 2, ALL_DAYS, (time(12), time(19)), AMS, 4, _ts(2026, 10, 11)
        )
    assert "28" in error.value.message
    assert "fewer weeks, days or times" in error.value.message


def test_generate_slots_26_rejected() -> None:
    # 13 Saturdays + 13 Sundays at one time = 26.
    with pytest.raises(InvalidSessionInput) as error:
        generate_slots(date(2026, 10, 12), 13, (SAT, SUN), (time(19),), AMS, 4, _ts(2026, 10, 11))
    assert "26" in error.value.message


def test_generate_slots_does_not_cap_weeks() -> None:
    # MAX_WEEKS is enforced by the command (caller), not here.
    result = generate_slots(
        date(2026, 10, 12), MAX_WEEKS + 2, (SAT,), (time(19),), AMS, 4, _ts(2026, 10, 11)
    )
    assert len(result) == MAX_WEEKS + 2


def test_generate_slots_skips_spring_forward_gap() -> None:
    # 2026-03-29 (Sunday): 02:00-03:00 doesn't exist in Amsterdam.
    result = generate_slots(
        date(2026, 3, 28), 1, (SAT, SUN), (time(2, 30), time(19)), AMS, 4, _ts(2026, 3, 1)
    )
    assert [r.start for r in result] == [
        _ts(2026, 3, 28, 2, 30),
        _ts(2026, 3, 28, 19),
        _ts(2026, 3, 29, 19),
    ]
    assert _utc(2026, 3, 29, 17) == result[2].start  # CEST (UTC+2)


def test_generate_slots_fall_back_uses_first_occurrence() -> None:
    # 2026-10-25 (Sunday): 02:00-03:00 happens twice; the first one is CEST (UTC+2).
    result = generate_slots(date(2026, 10, 25), 1, (SUN,), (time(2, 30),), AMS, 4, _ts(2026, 10, 1))
    assert [r.start for r in result] == [_utc(2026, 10, 25, 0, 30)]
    # Real seconds: the 4 hours span the repeated hour.
    assert result[0].end == _utc(2026, 10, 25, 4, 30)
