from __future__ import annotations

import pytest

from ninja_bot.activity.formatting import (
    ACTIVE_DAY,
    INACTIVE_DAY,
    day_strip,
    format_hours,
)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0m"),
        (59.9, "0m"),
        (60, "1m"),
        (45 * 60, "45m"),
        (45 * 60 + 59, "45m"),
        (3600 + 5 * 60, "1h 05m"),
        (12 * 3600 + 30 * 60, "12h 30m"),
        (103 * 3600 + 7 * 60 + 59.99, "103h 07m"),
        (-1, "0m"),
        (-3600, "0m"),
        (float("nan"), "0m"),
        (float("-inf"), "0m"),
        (float("inf"), "0m"),
    ],
)
def test_format_hours(seconds: float, expected: str) -> None:
    assert format_hours(seconds) == expected


def test_day_strip_groups_weeks() -> None:
    strip = day_strip({1, 8, 31}, 31)
    groups = strip.split(" ")
    assert [len(group) for group in groups] == [7, 7, 7, 7, 3]
    assert groups[0] == ACTIVE_DAY + INACTIVE_DAY * 6
    assert groups[1] == ACTIVE_DAY + INACTIVE_DAY * 6
    assert groups[4] == INACTIVE_DAY * 2 + ACTIVE_DAY


def test_day_strip_february_has_four_full_weeks() -> None:
    strip = day_strip(set(range(1, 29)), 28)
    assert strip == " ".join([ACTIVE_DAY * 7] * 4)


def test_day_strip_ignores_days_outside_month() -> None:
    assert day_strip({0, 30, 31}, 29) == " ".join([INACTIVE_DAY * 7] * 4 + [INACTIVE_DAY])


def test_day_strip_empty_month() -> None:
    assert day_strip(set(), 0) == ""
