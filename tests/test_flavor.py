import random

import pytest

from ninja_bot.flavor import LinePicker

POOL = ("a {x}", "b {x}", "c {x}")


def test_never_repeats_in_a_row() -> None:
    picker = LinePicker(random.Random(1))
    picks = [picker.pick(POOL, x=1) for _ in range(200)]
    assert all(a != b for a, b in zip(picks, picks[1:], strict=False))
    assert set(picks) == {"a 1", "b 1", "c 1"}


def test_formats_fields() -> None:
    assert LinePicker(random.Random(0)).pick(("hi {name}",), name="Bo") == "hi Bo"


def test_same_seed_same_picks() -> None:
    first = LinePicker(random.Random(7))
    second = LinePicker(random.Random(7))
    assert [first.pick(POOL, x=0) for _ in range(10)] == [second.pick(POOL, x=0) for _ in range(10)]


def test_single_line_pool_repeats() -> None:
    picker = LinePicker()
    assert [picker.pick(("only",)) for _ in range(3)] == ["only"] * 3


def test_empty_pool_raises() -> None:
    with pytest.raises(ValueError):
        LinePicker().pick(())
