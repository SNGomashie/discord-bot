from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from ninja_bot.cs2.client import StatsProvider
from ninja_bot.cs2.formatting import MISSING, format_rating
from ninja_bot.cs2.inventory import InventoryProvider
from ninja_bot.cs2.models import (
    Cs2Error,
    PrivateInventoryError,
    Profile,
    RateLimitedError,
    RecentMatch,
)
from ninja_bot.cs2.prices import PriceBook
from ninja_bot.cs2.valuation import value_inventory

log = logging.getLogger(__name__)

TrendDirection = Literal["up", "down", "flat"]
StreakKind = Literal["win", "loss"]

TREND_WINDOW = 5
MIN_WINDOW_RATINGS = 3
TREND_THRESHOLD = 0.01
# Absorbs float noise so a delta of exactly 0.01 counts as a change.
_TREND_EPSILON = 1e-9
MIN_SHOWN_STREAK = 3
# Profiles are fetched one by one, so this bounds the requests per /leaderboard.
MAX_LEADERBOARD_LINKS = 25
# /skinboard: inventories are fetched one by one too (cache misses are paced 4 s apart).
SKINBOARD_LIMIT = 25
SKINBOARD_BUDGET_SECONDS = 60.0

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
class SquadMember:
    """A linked server member who played in an announced match."""

    discord_id: int
    steam64_id: str


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


@dataclass(frozen=True)
class SquadFetch:
    entries: list[SquadEntry]
    # Profiles that failed with a CS2 error (private, unknown, API error).
    skipped: int
    # Links never fetched because Leetify rate limited us (0 if not rate limited).
    not_loaded: int
    rate_limited: bool


async def fetch_squad(
    provider: StatsProvider, links: Mapping[int, str], limit: int = MAX_LEADERBOARD_LINKS
) -> SquadFetch:
    """Fetch linked profiles one by one (lowest Discord ID first, at most `limit`).

    CS2 errors skip the player; a rate limit stops fetching and keeps what was collected.
    """
    wanted = sorted(links.items())[:limit]
    entries: list[SquadEntry] = []
    skipped = 0
    for index, (discord_id, steam64_id) in enumerate(wanted):
        try:
            profile = await provider.get_profile(steam64_id)
        except RateLimitedError:
            log.info("Leaderboard stopped early: Leetify rate limit")
            return SquadFetch(entries, skipped, len(wanted) - index, rate_limited=True)
        except Cs2Error as exc:
            log.info("Leaderboard skipped a player: %s", type(exc).__name__)
            skipped += 1
            continue
        entries.append(SquadEntry(discord_id, profile))
    return SquadFetch(entries, skipped, 0, rate_limited=False)


def leaderboard_notes(fetch: SquadFetch, total_links: int) -> list[str]:
    """Footer notes for players missing from the leaderboard."""
    notes: list[str] = []
    if fetch.skipped:
        notes.append(f"Skipped: {fetch.skipped} (private or unavailable)")
    if fetch.rate_limited:
        notes.append(f"Stopped early, Leetify is rate limiting us: {fetch.not_loaded} not loaded")
    if total_links > MAX_LEADERBOARD_LINKS:
        notes.append(f"Only the first {MAX_LEADERBOARD_LINKS} linked players are checked")
    return notes


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


@dataclass(frozen=True)
class WorthEntry:
    """A linked member's inventory worth in minor units (`name` None = unknown)."""

    discord_id: int
    name: str | None
    steam_total: int
    skinport_total: int


@dataclass(frozen=True)
class WorthRow:
    rank: int
    entry: WorthEntry


def rank_worth(entries: Sequence[WorthEntry]) -> list[WorthRow]:
    """Rank members by Steam total, then Skinport total, richest first.

    Members with the same two totals share a rank (1, 1, 3) and are ordered by name,
    then Discord ID.
    """
    ordered = sorted(
        entries,
        key=lambda e: (
            -e.steam_total,
            -e.skinport_total,
            (e.name or "").casefold(),
            e.name or "",
            e.discord_id,
        ),
    )
    rows: list[WorthRow] = []
    for position, entry in enumerate(ordered, start=1):
        previous = rows[-1] if rows else None
        if previous is not None and _worth(previous.entry) == _worth(entry):
            rank = previous.rank
        else:
            rank = position
        rows.append(WorthRow(rank, entry))
    return rows


def _worth(entry: WorthEntry) -> tuple[int, int]:
    return entry.steam_total, entry.skinport_total


@dataclass(frozen=True)
class SkinboardFetch:
    # Discord ID -> (steam_total, skinport_total) for the inventories fetched.
    worth: dict[int, tuple[int, int]]
    # Private or otherwise unavailable inventories.
    skipped: int
    # Links never fetched because the budget ran out or Steam rate limited us.
    not_loaded: int
    rate_limited: bool


async def fetch_skinboard(
    provider: InventoryProvider,
    book: PriceBook,
    links: Mapping[int, str],
    limit: int = SKINBOARD_LIMIT,
    budget_seconds: float = SKINBOARD_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> SkinboardFetch:
    """Value linked inventories one by one (lowest Discord ID first, at most `limit`); never
    raises.

    Private and unavailable inventories are skipped. A `RateLimitedError` stops at once and
    no new fetch starts once `budget_seconds` have passed; the rest count as not loaded.
    """
    wanted = sorted(links.items())[:limit]
    deadline = clock() + budget_seconds
    worth: dict[int, tuple[int, int]] = {}
    skipped = 0
    for index, (discord_id, steam64_id) in enumerate(wanted):
        if clock() >= deadline:
            log.info("Skinboard budget used up; %d not loaded", len(wanted) - index)
            return SkinboardFetch(worth, skipped, len(wanted) - index, rate_limited=False)
        try:
            items = await provider.get_inventory(steam64_id)
        except PrivateInventoryError:
            skipped += 1
            continue
        except RateLimitedError:
            log.info("Skinboard stopped early: Steam rate limit")
            return SkinboardFetch(worth, skipped, len(wanted) - index, rate_limited=True)
        except Exception as exc:
            log.warning("Skinboard skipped an inventory: %s", type(exc).__name__)
            skipped += 1
            continue
        valuation = value_inventory(items, book, 0)
        worth[discord_id] = (valuation.steam_total, valuation.skinport_total)
    return SkinboardFetch(worth, skipped, 0, rate_limited=False)


def skinboard_notes(fetch: SkinboardFetch, total_links: int) -> list[str]:
    """Footer notes for members missing from the skinboard."""
    notes: list[str] = []
    if fetch.skipped:
        notes.append(f"{fetch.skipped} skipped (private or unavailable)")
    if fetch.not_loaded:
        reason = "Steam is rate limiting us" if fetch.rate_limited else "out of time"
        notes.append(f"Stopped early, {reason}: {fetch.not_loaded} not loaded")
    if total_links > SKINBOARD_LIMIT:
        notes.append(f"Only the first {SKINBOARD_LIMIT} linked members are checked")
    return notes
