from __future__ import annotations

import ast
from datetime import time
from pathlib import Path

import pytest

from ninja_bot.sessions import slots
from ninja_bot.sessions.slots import (
    INVALID_DAYS_MESSAGE,
    INVALID_TIMES_MESSAGE,
    MAX_DAYS_LENGTH,
    MAX_TIMES_LENGTH,
    InvalidSessionInput,
    parse_days,
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
