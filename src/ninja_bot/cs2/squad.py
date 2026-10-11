from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ninja_bot.cs2.formatting import format_rating
from ninja_bot.cs2.models import RecentMatch

TrendDirection = Literal["up", "down", "flat"]
StreakKind = Literal["win", "loss"]

TREND_WINDOW = 5
MIN_WINDOW_RATINGS = 3
TREND_THRESHOLD = 0.01
# Absorbs float noise so a delta of exactly 0.01 counts as a change.
_TREND_EPSILON = 1e-9
MIN_SHOWN_STREAK = 3

_ARROWS: dict[TrendDirection, str] = {"up": "↑", "down": "↓", "flat": "→"}
_STREAK_EMOJI: dict[StreakKind, str] = {"win": "🔥", "loss": "🧊"}


@dataclass(frozen=True)
class Trend:
    direction: TrendDirection
    # Newer window average minus older window average.
    delta: float
    streak_kind: StreakKind | None
    streak_length: int


def _window_average(matches: Sequence[RecentMatch]) -> float | None:
    ratings = [m.leetify_rating for m in matches if m.leetify_rating is not None]
    if len(ratings) < MIN_WINDOW_RATINGS:
        return None
    return sum(ratings) / len(ratings)


def _streak(recent: Sequence[RecentMatch]) -> tuple[StreakKind | None, int]:
    if not recent:
        return None, 0
    kind = recent[0].outcome
    if kind != "win" and kind != "loss":
        return None, 0
    length = 0
    for match in recent:
        if match.outcome != kind:
            break
        length += 1
    return kind, length


def compute_trend(recent: Sequence[RecentMatch]) -> Trend | None:
    """Recent form of a player from their newest-first `recent` matches.

    Compares the average rating of the newest `TREND_WINDOW` matches with the
    `TREND_WINDOW` before them (or whatever remains); None when either window has
    fewer than `MIN_WINDOW_RATINGS` ratings.
    """
    newer = _window_average(recent[:TREND_WINDOW])
    older = _window_average(recent[TREND_WINDOW : 2 * TREND_WINDOW])
    if newer is None or older is None:
        return None
    delta = newer - older
    direction: TrendDirection
    if delta >= TREND_THRESHOLD - _TREND_EPSILON:
        direction = "up"
    elif delta <= -TREND_THRESHOLD + _TREND_EPSILON:
        direction = "down"
    else:
        direction = "flat"
    streak_kind, streak_length = _streak(recent)
    return Trend(direction, delta, streak_kind, streak_length)


def format_trend(trend: Trend | None) -> str:
    """`↑ +0.04`, `↓ -0.02` or `→`, plus ` 🔥3` / ` 🧊3` for streaks of 3+."""
    if trend is None:
        return ""
    text = _ARROWS[trend.direction]
    if trend.direction != "flat":
        text += f" {format_rating(trend.delta)}"
    if trend.streak_kind is not None and trend.streak_length >= MIN_SHOWN_STREAK:
        text += f" {_STREAK_EMOJI[trend.streak_kind]}{trend.streak_length}"
    return text
