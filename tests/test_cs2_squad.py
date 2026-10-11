from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from ninja_bot.cs2.models import (
    ApiError,
    Cs2Error,
    Match,
    MatchOutcome,
    PlayerNotFoundError,
    PrivateProfileError,
    Profile,
    RateLimitedError,
    RecentMatch,
)
from ninja_bot.cs2.squad import (
    MAX_LEADERBOARD_LINKS,
    LeaderboardRow,
    Metric,
    SquadEntry,
    SquadFetch,
    Trend,
    compute_trend,
    fetch_squad,
    format_metric_value,
    format_trend,
    leaderboard_notes,
    metric_label,
    rank_squad,
)


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


def _profile(
    name: str | None = "Player",
    steam64_id: str = "76561198000000001",
    ratings: list[float | None] | None = None,
    **values: float | None,
) -> Profile:
    fields: dict[str, float | None] = {
        "winrate": None,
        "aim": None,
        "positioning": None,
        "utility": None,
        "clutch": None,
        "opening": None,
    }
    fields.update(values)
    return Profile(
        steam64_id=steam64_id,
        name=name,
        total_matches=None,
        ranks=None,
        recent_matches=tuple(_match(i, r) for i, r in enumerate(ratings or [])),
        **fields,
    )


def _entry(discord_id: int, name: str | None, **kwargs: Any) -> SquadEntry:
    return SquadEntry(discord_id, _profile(name, **kwargs))


@pytest.mark.parametrize(
    ("metric", "low", "high"),
    [
        (Metric.AIM, {"aim": 40.0}, {"aim": 80.0}),
        (Metric.POSITIONING, {"positioning": 40.0}, {"positioning": 80.0}),
        (Metric.UTILITY, {"utility": 40.0}, {"utility": 80.0}),
        (Metric.CLUTCH, {"clutch": -0.05}, {"clutch": 0.1}),
        (Metric.OPENING, {"opening": -0.05}, {"opening": 0.1}),
        (Metric.WINRATE, {"winrate": 0.4}, {"winrate": 0.62}),
        (Metric.RATING, {"ratings": [0.0, -0.02]}, {"ratings": [0.05, 0.03]}),
    ],
)
def test_rank_squad_each_metric(metric: Metric, low: dict[str, Any], high: dict[str, Any]) -> None:
    rows = rank_squad([_entry(1, "Low", **low), _entry(2, "High", **high)], metric)
    assert [(row.rank, row.discord_id, row.name) for row in rows] == [
        (1, 2, "High"),
        (2, 1, "Low"),
    ]


def test_rank_squad_rating_is_average_of_rated_recent_matches() -> None:
    rows = rank_squad([_entry(1, "A", ratings=[0.06, None, 0.02])], Metric.RATING)
    assert rows[0].value == pytest.approx(0.04)


def test_rank_squad_ties_share_rank_and_sort_by_name() -> None:
    rows = rank_squad(
        [
            _entry(1, "zed", aim=70.0),
            _entry(2, "Bob", aim=70.0),
            _entry(3, "carl", aim=50.0),
            _entry(4, "Ann", aim=90.0),
            _entry(5, "amy", aim=90.0),
        ],
        Metric.AIM,
    )
    assert [(row.rank, row.name) for row in rows] == [
        (1, "amy"),
        (1, "Ann"),
        (3, "Bob"),
        (3, "zed"),
        (5, "carl"),
    ]


def test_rank_squad_missing_values_last_without_rank() -> None:
    rows = rank_squad(
        [
            _entry(1, "Zoe"),
            _entry(2, "Max", aim=10.0),
            _entry(3, "Adam", aim=float("nan")),
            _entry(4, "Kim", aim=60.0),
        ],
        Metric.AIM,
    )
    assert [(row.rank, row.name, row.value) for row in rows] == [
        (1, "Kim", 60.0),
        (2, "Max", 10.0),
        (None, "Adam", None),
        (None, "Zoe", None),
    ]


def test_rank_squad_rating_missing_without_rated_matches() -> None:
    rows = rank_squad([_entry(1, "A", ratings=[None])], Metric.RATING)
    assert rows[0].rank is None
    assert rows[0].value is None


def test_rank_squad_single_entry_and_name_fallback() -> None:
    rows = rank_squad([_entry(7, None, winrate=0.5)], Metric.WINRATE)
    assert rows == [
        LeaderboardRow(rank=1, discord_id=7, name="76561198000000001", value=0.5, trend=None)
    ]


def test_rank_squad_empty() -> None:
    assert rank_squad([], Metric.RATING) == []


def test_rank_squad_includes_trend() -> None:
    ratings: list[float | None] = [0.05] * 5 + [0.01] * 5
    rows = rank_squad([_entry(1, "A", ratings=ratings)], Metric.RATING)
    trend = rows[0].trend
    assert trend is not None
    assert trend.direction == "up"


def test_metric_values_and_labels() -> None:
    assert [m.value for m in Metric] == [
        "rating",
        "aim",
        "positioning",
        "utility",
        "clutch",
        "opening",
        "winrate",
    ]
    assert all(metric_label(m) for m in Metric)


@pytest.mark.parametrize(
    ("metric", "value", "expected"),
    [
        (Metric.RATING, 0.054, "+0.05"),
        (Metric.RATING, -0.001, "+0.00"),
        (Metric.CLUTCH, -0.031, "-0.03"),
        (Metric.OPENING, 0.1, "+0.10"),
        (Metric.AIM, 72.4, "72"),
        (Metric.POSITIONING, 55.6, "56"),
        (Metric.UTILITY, 0.0, "0"),
        (Metric.WINRATE, 0.62, "62%"),
        (Metric.WINRATE, None, "-"),
        (Metric.AIM, float("nan"), "-"),
    ],
)
def test_format_metric_value(metric: Metric, value: float | None, expected: str) -> None:
    assert format_metric_value(metric, value) == expected


# --- fetch_squad / leaderboard_notes ---


def _steam_id(discord_id: int) -> str:
    return f"7656119800000{discord_id:04d}"


class _SquadProvider:
    """Profile or error per Steam ID; records the order of `get_profile` calls."""

    def __init__(self, results: Mapping[int, Profile | Exception]) -> None:
        self.results = {_steam_id(d): r for d, r in results.items()}
        self.calls: list[str] = []
        self.active = 0
        self.peak = 0

    async def get_profile(self, steam64_id: str) -> Profile:
        self.calls.append(steam64_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        result = self.results[steam64_id]
        if isinstance(result, Exception):
            raise result
        return result

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        raise NotImplementedError

    async def get_match(self, game_id: str) -> Match:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


def _links(results: Mapping[int, object]) -> dict[int, str]:
    return {d: _steam_id(d) for d in results}


def _squad(*discord_ids: int) -> dict[int, Profile | Exception]:
    return {d: _profile(f"P{d}", _steam_id(d)) for d in discord_ids}


def _ids(fetch: SquadFetch) -> list[int]:
    return [entry.discord_id for entry in fetch.entries]


async def test_fetch_squad_lowest_discord_id_first_one_at_a_time() -> None:
    results = _squad(3, 1, 2)
    provider = _SquadProvider(results)
    fetch = await fetch_squad(provider, _links(results))
    assert provider.calls == [_steam_id(1), _steam_id(2), _steam_id(3)]
    assert provider.peak == 1
    assert _ids(fetch) == [1, 2, 3]
    assert fetch == SquadFetch(fetch.entries, 0, 0, rate_limited=False)


@pytest.mark.parametrize(
    "error", [PrivateProfileError(), PlayerNotFoundError(), ApiError()], ids=type
)
async def test_fetch_squad_skips_cs2_errors(error: Cs2Error) -> None:
    results = _squad(1, 3)
    results[2] = error
    results[4] = ApiError()
    provider = _SquadProvider(results)
    fetch = await fetch_squad(provider, _links(results))
    assert len(provider.calls) == 4
    assert _ids(fetch) == [1, 3]
    assert fetch.skipped == 2
    assert not fetch.rate_limited
    assert fetch.not_loaded == 0


async def test_fetch_squad_rate_limit_stops_and_keeps_collected() -> None:
    results = _squad(1, 2, 4, 5)
    results[3] = RateLimitedError()
    provider = _SquadProvider(results)
    fetch = await fetch_squad(provider, _links(results))
    assert provider.calls == [_steam_id(1), _steam_id(2), _steam_id(3)]
    assert _ids(fetch) == [1, 2]
    assert fetch.rate_limited
    assert fetch.not_loaded == 3
    assert fetch.skipped == 0


async def test_fetch_squad_rate_limit_counts_only_wanted_links() -> None:
    results = _squad(*range(2, 11))
    results[1] = RateLimitedError()
    provider = _SquadProvider(results)
    fetch = await fetch_squad(provider, _links(results), limit=4)
    assert fetch.entries == []
    assert fetch.not_loaded == 4


async def test_fetch_squad_unexpected_error_propagates() -> None:
    results = _squad(1)
    results[2] = RuntimeError("boom")
    provider = _SquadProvider(results)
    with pytest.raises(RuntimeError):
        await fetch_squad(provider, _links(results))


async def test_fetch_squad_caps_at_limit() -> None:
    count = MAX_LEADERBOARD_LINKS + 5
    results = _squad(*range(1, count + 1))
    provider = _SquadProvider(results)
    fetch = await fetch_squad(provider, _links(results))
    assert MAX_LEADERBOARD_LINKS == 25
    assert provider.calls == [_steam_id(d) for d in range(1, MAX_LEADERBOARD_LINKS + 1)]
    assert len(fetch.entries) == MAX_LEADERBOARD_LINKS
    small = await fetch_squad(_SquadProvider(results), _links(results), limit=2)
    assert _ids(small) == [1, 2]


async def test_fetch_squad_empty() -> None:
    fetch = await fetch_squad(_SquadProvider({}), {})
    assert fetch == SquadFetch([], 0, 0, rate_limited=False)


@pytest.mark.parametrize(
    ("fetch", "total", "expected"),
    [
        (SquadFetch([], 0, 0, rate_limited=False), 3, []),
        (SquadFetch([], 2, 0, rate_limited=False), 3, ["Skipped: 2 (private or unavailable)"]),
        (
            SquadFetch([], 0, 2, rate_limited=True),
            3,
            ["Stopped early, Leetify is rate limiting us: 2 not loaded"],
        ),
        (
            SquadFetch([], 1, 1, rate_limited=True),
            3,
            [
                "Skipped: 1 (private or unavailable)",
                "Stopped early, Leetify is rate limiting us: 1 not loaded",
            ],
        ),
        (
            SquadFetch([], 0, 0, rate_limited=False),
            MAX_LEADERBOARD_LINKS,
            [],
        ),
        (
            SquadFetch([], 0, 0, rate_limited=False),
            MAX_LEADERBOARD_LINKS + 1,
            ["Only the first 25 linked players are checked"],
        ),
    ],
)
def test_leaderboard_notes(fetch: SquadFetch, total: int, expected: list[str]) -> None:
    assert leaderboard_notes(fetch, total) == expected
