from __future__ import annotations

import datetime
from collections.abc import Collection, Sequence

import pytest

from ninja_bot.cs2.models import (
    ApiError,
    Match,
    PlayerMatchStats,
    PrivateProfileError,
    Profile,
    RateLimitedError,
    TeamScore,
)
from ninja_bot.cs2.poller import MatchPoller, NewMatch
from ninja_bot.cs2.squad import SquadMember

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
STEAM_C = "76561198000000003"
STRANGER = "76561198000000099"

NOW = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=datetime.UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW.timestamp()

    def __call__(self) -> float:
        return self.now


def ago(minutes: float) -> datetime.datetime:
    return NOW - datetime.timedelta(minutes=minutes)


def make_match(
    match_id: str,
    *steam64_ids: str,
    finished_at: datetime.datetime | None = None,
    teams: bool = True,
) -> Match:
    players = tuple(
        PlayerMatchStats(steam64_id=s, team_number=(2 + index % 2) if teams else None)
        for index, s in enumerate(steam64_ids)
    )
    return Match(
        id=match_id,
        map_name="de_mirage",
        finished_at=finished_at,
        team_scores=(TeamScore(2, 13), TeamScore(3, 7)),
        players=players,
    )


class FakeStats:
    def __init__(self) -> None:
        self.history: dict[str, list[Match]] = {}
        self.errors: dict[str, Exception] = {}
        self.by_id: dict[str, Match] = {}
        self.matches_calls: list[str] = []
        self.match_calls: list[str] = []

    async def get_profile(self, steam64_id: str) -> Profile:
        raise NotImplementedError

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        self.matches_calls.append(steam64_id)
        if steam64_id in self.errors:
            raise self.errors[steam64_id]
        return list(self.history.get(steam64_id, []))

    async def get_match(self, game_id: str) -> Match:
        self.match_calls.append(game_id)
        if game_id in self.by_id:
            return self.by_id[game_id]
        raise ApiError()

    async def aclose(self) -> None:
        pass


class FakeStore:
    def __init__(self, links: dict[int, str] | None = None) -> None:
        self.links = dict(links or {})
        self.seeded: set[str] = set()
        self.posted: list[str] = []

    async def get(self, discord_id: int) -> str | None:
        return self.links.get(discord_id)

    async def set(self, discord_id: int, steam64_id: str) -> None:
        self.links[discord_id] = steam64_id

    async def remove(self, discord_id: int) -> bool:
        return self.links.pop(discord_id, None) is not None

    async def all_links(self) -> dict[int, str]:
        return dict(self.links)

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for discord_id in sorted(self.links):
            steam64_id = self.links[discord_id]
            if steam64_id in steam64_ids:
                result.setdefault(steam64_id, discord_id)
        return result

    async def is_seeded(self, steam64_id: str) -> bool:
        return steam64_id in self.seeded

    async def mark_seeded(self, steam64_id: str) -> None:
        self.seeded.add(steam64_id)

    async def filter_unposted(self, match_ids: Sequence[str]) -> list[str]:
        return [match_id for match_id in match_ids if match_id not in self.posted]

    async def mark_posted(self, match_ids: Sequence[str]) -> None:
        for match_id in match_ids:
            if match_id not in self.posted:
                self.posted.append(match_id)


def make_poller(stats: FakeStats, store: FakeStore, **kwargs: int) -> tuple[MatchPoller, FakeClock]:
    clock = FakeClock()
    return MatchPoller(stats, store, clock, **kwargs), clock


async def confirm_all(poller: MatchPoller, new: list[NewMatch]) -> None:
    for new_match in new:
        await poller.confirm(new_match)


async def test_no_links_returns_nothing() -> None:
    stats = FakeStats()
    poller, _ = make_poller(stats, FakeStore())
    assert await poller.poll() == []
    assert stats.matches_calls == []


async def test_first_poll_seeds_silently() -> None:
    stats = FakeStats()
    stats.history[STEAM_A] = [make_match("m1", STEAM_A, finished_at=ago(5))]
    store = FakeStore({1: STEAM_A})
    poller, _ = make_poller(stats, store)

    assert await poller.poll() == []
    assert store.seeded == {STEAM_A}
    assert store.posted == ["m1"]
    assert await poller.poll() == []


async def test_second_poll_announces_new_match_once() -> None:
    stats = FakeStats()
    old = make_match("m1", STEAM_A, STRANGER, finished_at=ago(60))
    stats.history[STEAM_A] = [old]
    store = FakeStore({1: STEAM_A})
    poller, _ = make_poller(stats, store)
    await poller.poll()

    new = make_match("m2", STEAM_A, STRANGER, finished_at=ago(5))
    stats.history[STEAM_A] = [new, old]
    result = await poller.poll()
    assert result == [NewMatch(new, (SquadMember(1, STEAM_A),))]

    # Not confirmed yet: still returned.
    assert await poller.poll() == result
    await poller.confirm(result[0])
    assert await poller.poll() == []


async def test_confirm_is_idempotent() -> None:
    store = FakeStore()
    poller, _ = make_poller(FakeStats(), store)
    new_match = NewMatch(make_match("m1", STEAM_A), ())
    await poller.confirm(new_match)
    await poller.confirm(new_match)
    assert store.posted == ["m1"]


async def test_shared_match_is_merged_with_full_squad() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A, 2: STEAM_B, 3: STEAM_C})
    store.seeded = {STEAM_A, STEAM_B, STEAM_C}
    shared = make_match("m1", STRANGER, STEAM_B, STEAM_A, finished_at=ago(5))
    stats.history = {STEAM_A: [shared], STEAM_B: [shared], STEAM_C: []}
    poller, _ = make_poller(stats, store)

    result = await poller.poll()
    assert len(result) == 1
    assert result[0].match == shared
    assert result[0].squad == (SquadMember(2, STEAM_B), SquadMember(1, STEAM_A))


async def test_partial_history_entry_is_completed_before_squad() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A, 2: STEAM_B})
    store.seeded = {STEAM_A}  # B was linked but has a private history, say
    stats.errors[STEAM_B] = PrivateProfileError()
    partial = make_match("m1", STEAM_A, finished_at=ago(5))
    full = make_match("m1", STEAM_A, STEAM_B, STRANGER, finished_at=ago(5))
    stats.history[STEAM_A] = [partial]
    stats.by_id["m1"] = full
    poller, _ = make_poller(stats, store)

    result = await poller.poll()
    assert stats.match_calls == ["m1"]
    assert result == [NewMatch(full, (SquadMember(1, STEAM_A), SquadMember(2, STEAM_B)))]


async def test_duplicate_links_use_lowest_discord_id() -> None:
    stats = FakeStats()
    store = FakeStore({7: STEAM_A, 3: STEAM_A})
    store.seeded = {STEAM_A}
    match = make_match("m1", STEAM_A, STRANGER, finished_at=ago(5))
    stats.history[STEAM_A] = [match]
    poller, _ = make_poller(stats, store)

    result = await poller.poll()
    assert stats.matches_calls == [STEAM_A]
    assert result[0].squad == (SquadMember(3, STEAM_A),)


async def test_stale_matches_are_marked_posted_and_dropped() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A})
    store.seeded = {STEAM_A}
    stale = make_match("old", STEAM_A, STRANGER, finished_at=ago(181))
    edge = make_match("edge", STEAM_A, STRANGER, finished_at=ago(180))
    undated = make_match("undated", STEAM_A, STRANGER)
    stats.history[STEAM_A] = [edge, stale, undated]
    poller, _ = make_poller(stats, store)

    result = await poller.poll()
    assert [new.match.id for new in result] == ["edge", "undated"]
    assert store.posted == ["old"]


async def test_max_per_cycle_oldest_first_with_carry_over() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A})
    store.seeded = {STEAM_A}
    matches = [
        make_match(f"m{minutes}", STEAM_A, STRANGER, finished_at=ago(minutes))
        for minutes in (10, 20, 30, 40)
    ]
    undated = make_match("undated", STEAM_A, STRANGER)
    stats.history[STEAM_A] = [undated, *matches]
    poller, _ = make_poller(stats, store, max_per_cycle=3)

    first = await poller.poll()
    assert [new.match.id for new in first] == ["m40", "m30", "m20"]
    await confirm_all(poller, first)

    second = await poller.poll()
    assert [new.match.id for new in second] == ["m10", "undated"]


async def test_player_errors_skip_that_player() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A, 2: STEAM_B, 3: STEAM_C})
    store.seeded = {STEAM_A, STEAM_C}
    stats.errors[STEAM_A] = PrivateProfileError()
    stats.errors[STEAM_B] = ApiError()
    match = make_match("m1", STEAM_C, STRANGER, finished_at=ago(5))
    stats.history[STEAM_C] = [match]
    poller, _ = make_poller(stats, store)

    result = await poller.poll()
    assert stats.matches_calls == [STEAM_A, STEAM_B, STEAM_C]
    assert [new.match.id for new in result] == ["m1"]
    assert STEAM_B not in store.seeded


async def test_rate_limit_stops_cycle_and_keeps_found_matches() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A, 2: STEAM_B, 3: STEAM_C})
    store.seeded = {STEAM_A}
    match = make_match("m1", STEAM_A, STRANGER, finished_at=ago(5))
    stats.history[STEAM_A] = [match]
    stats.errors[STEAM_B] = RateLimitedError()
    poller, _ = make_poller(stats, store)

    result = await poller.poll()
    assert stats.matches_calls == [STEAM_A, STEAM_B]
    assert [new.match.id for new in result] == ["m1"]
    assert store.seeded == {STEAM_A}


async def test_unexpected_errors_propagate() -> None:
    stats = FakeStats()
    stats.errors[STEAM_A] = RuntimeError("boom")
    poller, _ = make_poller(stats, FakeStore({1: STEAM_A}))
    with pytest.raises(RuntimeError):
        await poller.poll()


async def test_poll_does_not_mark_returned_matches_posted() -> None:
    stats = FakeStats()
    store = FakeStore({1: STEAM_A})
    store.seeded = {STEAM_A}
    stats.history[STEAM_A] = [make_match("m1", STEAM_A, STRANGER, finished_at=ago(5))]
    poller, _ = make_poller(stats, store)
    await poller.poll()
    assert store.posted == []
