from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from ninja_bot.cs2.formatting import MISSING, format_rating
from ninja_bot.cs2.models import Profile, RecentMatch

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


class Metric(StrEnum):
    RATING = "rating"
    AIM = "aim"
    POSITIONING = "positioning"
    UTILITY = "utility"
    CLUTCH = "clutch"
    OPENING = "opening"
    WINRATE = "winrate"


_METRIC_LABELS: dict[Metric, str] = {
    Metric.RATING: "Leetify rating",
    Metric.AIM: "Aim",
    Metric.POSITIONING: "Positioning",
    Metric.UTILITY: "Utility",
    Metric.CLUTCH: "Clutch",
    Metric.OPENING: "Opening duels",
    Metric.WINRATE: "Winrate",
}

# 0-100 skill scores; the other metrics are signed ratings except winrate.
_SCORE_METRICS = frozenset({Metric.AIM, Metric.POSITIONING, Metric.UTILITY})


@dataclass(frozen=True)
class SquadEntry:
    discord_id: int
    profile: Profile


@dataclass(frozen=True)
class LeaderboardRow:
    # None for players without a value (listed last, unranked).
    rank: int | None
    discord_id: int
    name: str
    value: float | None
    trend: Trend | None


def _average_rating(profile: Profile) -> float | None:
    ratings = [m.leetify_rating for m in profile.recent_matches if m.leetify_rating is not None]
    if not ratings:
        return None
    return sum(ratings) / len(ratings)


_METRIC_VALUES: dict[Metric, Callable[[Profile], float | None]] = {
    Metric.RATING: _average_rating,
    Metric.AIM: lambda p: p.aim,
    Metric.POSITIONING: lambda p: p.positioning,
    Metric.UTILITY: lambda p: p.utility,
    Metric.CLUTCH: lambda p: p.clutch,
    Metric.OPENING: lambda p: p.opening,
    Metric.WINRATE: lambda p: p.winrate,
}


def metric_value(profile: Profile, metric: Metric) -> float | None:
    """The profile's value for `metric`; None when missing or not a finite number."""
    value = _METRIC_VALUES[metric](profile)
    if value is None or not math.isfinite(value):
        return None
    return value


def rank_squad(entries: Sequence[SquadEntry], metric: Metric) -> list[LeaderboardRow]:
    """Rank players by `metric`, best first.

    Ties share a rank (1, 1, 3) and are ordered by name; players without a value
    come last with `rank=None`.
    """
    rows = [
        LeaderboardRow(
            rank=None,
            discord_id=entry.discord_id,
            name=entry.profile.name or entry.profile.steam64_id,
            value=metric_value(entry.profile, metric),
            trend=compute_trend(entry.profile.recent_matches),
        )
        for entry in entries
    ]
    rows.sort(key=lambda row: (row.name.casefold(), row.name, row.discord_id))
    valued = sorted(
        (row for row in rows if row.value is not None),
        key=lambda row: -(row.value or 0.0),
    )
    missing = [row for row in rows if row.value is None]
    ranked: list[LeaderboardRow] = []
    for position, row in enumerate(valued, start=1):
        previous = ranked[-1] if ranked else None
        if previous is not None and previous.value == row.value:
            rank = previous.rank
        else:
            rank = position
        ranked.append(replace(row, rank=rank))
    return ranked + missing


def metric_label(metric: Metric) -> str:
    return _METRIC_LABELS[metric]


def format_metric_value(metric: Metric, value: float | None) -> str:
    """`+0.05` for ratings, `72` for 0-100 skill scores, `62%` for winrate, `-` if unknown."""
    if value is None or not math.isfinite(value):
        return MISSING
    if metric is Metric.WINRATE:
        return f"{value * 100:.0f}%"
    if metric in _SCORE_METRICS:
        return f"{value:.0f}"
    return format_rating(value)
