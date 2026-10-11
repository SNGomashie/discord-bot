from __future__ import annotations

import pytest

from ninja_bot.cs2.models import MatchOutcome, RecentMatch
from ninja_bot.cs2.squad import Trend, compute_trend, format_trend


def _match(
    index: int, rating: float | None = 0.0, outcome: MatchOutcome | None = "tie"
) -> RecentMatch:
    return RecentMatch(
        id=f"m{index}",
        finished_at=None,
        map_name="de_mirage",
        outcome=outcome,
        leetify_rating=rating,
        score=None,
    )


def _history(
    newer: list[float | None],
    older: list[float | None],
    outcomes: list[MatchOutcome | None] | None = None,
) -> list[RecentMatch]:
    ratings = newer + older
    results = outcomes or []
    return [
        _match(i, rating, results[i] if i < len(results) else "tie")
        for i, rating in enumerate(ratings)
    ]


def test_trend_up() -> None:
    trend = compute_trend(_history([0.05] * 5, [0.01] * 5))
    assert trend is not None
    assert trend.direction == "up"
    assert trend.delta == pytest.approx(0.04)


def test_trend_down() -> None:
    trend = compute_trend(_history([-0.02] * 5, [0.0] * 5))
    assert trend is not None
    assert trend.direction == "down"
    assert trend.delta == pytest.approx(-0.02)


def test_trend_flat() -> None:
    trend = compute_trend(_history([0.035] * 5, [0.03] * 5))
    assert trend is not None
    assert trend.direction == "flat"


@pytest.mark.parametrize(
    ("newer", "older", "expected"),
    [
        (0.05, 0.04, "up"),
        (0.03, 0.04, "down"),
        (0.0499, 0.04, "flat"),
        (0.0301, 0.04, "flat"),
    ],
)
def test_trend_boundaries(newer: float, older: float, expected: str) -> None:
    trend = compute_trend(_history([newer] * 5, [older] * 5))
    assert trend is not None
    assert trend.direction == expected


def test_only_ten_newest_matches_count() -> None:
    history = _history([0.02] * 5, [0.02] * 5) + [_match(10, -1.0)] * 5
    trend = compute_trend(history)
    assert trend is not None
    assert trend.direction == "flat"


def test_short_history_uses_remaining_matches() -> None:
    trend = compute_trend(_history([0.04] * 5, [0.0] * 3))
    assert trend is not None
    assert trend.direction == "up"


@pytest.mark.parametrize("count", [0, 3, 5, 7])
def test_too_short_history_has_no_trend(count: int) -> None:
    assert compute_trend(_history([0.01] * 5, [])[:count]) is None


def test_missing_ratings_are_ignored_for_averages() -> None:
    trend = compute_trend(_history([0.04, None, 0.04, None, 0.04], [0.0, 0.0, None, 0.0, 0.0]))
    assert trend is not None
    assert trend.delta == pytest.approx(0.04)


@pytest.mark.parametrize(
    ("newer", "older"),
    [
        ([0.04, None, 0.04, None, None], [0.0] * 5),
        ([0.04] * 5, [None, None, 0.0, 0.0, None]),
    ],
)
def test_too_few_ratings_in_a_window_has_no_trend(
    newer: list[float | None], older: list[float | None]
) -> None:
    assert compute_trend(_history(newer, older)) is None


def test_win_streak() -> None:
    outcomes: list[MatchOutcome | None] = ["win", "win", "win", "loss", "win"]
    trend = compute_trend(_history([0.0] * 5, [0.0] * 5, outcomes))
    assert trend is not None
    assert (trend.streak_kind, trend.streak_length) == ("win", 3)


def test_loss_streak() -> None:
    outcomes: list[MatchOutcome | None] = ["loss"] * 4 + ["tie"]
    trend = compute_trend(_history([0.0] * 5, [0.0] * 5, outcomes))
    assert trend is not None
    assert (trend.streak_kind, trend.streak_length) == ("loss", 4)


@pytest.mark.parametrize("breaker", ["tie", None])
def test_tie_or_unknown_breaks_streak(breaker: MatchOutcome | None) -> None:
    outcomes: list[MatchOutcome | None] = ["win", "win", breaker, "win", "win"]
    trend = compute_trend(_history([0.0] * 5, [0.0] * 5, outcomes))
    assert trend is not None
    assert (trend.streak_kind, trend.streak_length) == ("win", 2)


@pytest.mark.parametrize("newest", ["tie", None])
def test_no_streak_when_newest_is_tie_or_unknown(newest: MatchOutcome | None) -> None:
    outcomes: list[MatchOutcome | None] = [newest, "win", "win", "win"]
    trend = compute_trend(_history([0.0] * 5, [0.0] * 5, outcomes))
    assert trend is not None
    assert (trend.streak_kind, trend.streak_length) == (None, 0)


def test_unrated_matches_still_count_for_streaks() -> None:
    outcomes: list[MatchOutcome | None] = ["win"] * 4
    trend = compute_trend(_history([None, 0.0, 0.0, 0.0, None], [0.0] * 5, outcomes))
    assert trend is not None
    assert trend.streak_length == 4


@pytest.mark.parametrize(
    ("trend", "expected"),
    [
        (None, ""),
        (Trend("up", 0.04, None, 0), "↑ +0.04"),
        (Trend("down", -0.02, None, 0), "↓ -0.02"),
        (Trend("flat", 0.004, None, 0), "→"),
        (Trend("up", 0.04, "win", 3), "↑ +0.04 🔥3"),
        (Trend("flat", 0.0, "loss", 5), "→ 🧊5"),
        (Trend("up", 0.04, "win", 2), "↑ +0.04"),
        (Trend("down", -0.02, "loss", 2), "↓ -0.02"),
    ],
)
def test_format_trend(trend: Trend | None, expected: str) -> None:
    assert format_trend(trend) == expected
