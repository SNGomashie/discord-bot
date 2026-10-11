from __future__ import annotations

import pytest

from ninja_bot.cs2.cache import CachedStatsProvider, TtlCache
from ninja_bot.cs2.client import StatsProvider
from ninja_bot.cs2.models import ApiError, Match, Profile

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_profile(steam64_id: str, name: str = "Ninja") -> Profile:
    return Profile(
        steam64_id=steam64_id,
        name=name,
        winrate=None,
        total_matches=None,
        aim=None,
        positioning=None,
        utility=None,
        clutch=None,
        opening=None,
        ranks=None,
        recent_matches=(),
    )


def make_match(match_id: str) -> Match:
    return Match(
        id=match_id,
        finished_at=None,
        map_name="de_mirage",
        team_scores=(),
        players=(),
    )


class FakeStats:
    def __init__(self) -> None:
        self.profile_calls: list[str] = []
        self.matches_calls: list[str] = []
        self.match_calls: list[str] = []
        self.error: Exception | None = None
        self.closed = 0

    async def get_profile(self, steam64_id: str) -> Profile:
        self.profile_calls.append(steam64_id)
        if self.error is not None:
            raise self.error
        return make_profile(steam64_id, name=f"call{len(self.profile_calls)}")

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        self.matches_calls.append(steam64_id)
        return [make_match(f"m{len(self.matches_calls)}")]

    async def get_match(self, game_id: str) -> Match:
        self.match_calls.append(game_id)
        return make_match(game_id)

    async def aclose(self) -> None:
        self.closed += 1


def test_ttl_cache_hit_and_miss() -> None:
    cache: TtlCache[str, int] = TtlCache(10, FakeClock())
    assert cache.get("a") is None
    cache.set("a", 1)
    assert cache.get("a") == 1


def test_ttl_cache_expires() -> None:
    clock = FakeClock()
    cache: TtlCache[str, int] = TtlCache(10, clock)
    cache.set("a", 1)
    clock.now += 9.9
    assert cache.get("a") == 1
    clock.now += 0.1
    assert cache.get("a") is None


def test_ttl_cache_set_refreshes_expiry() -> None:
    clock = FakeClock()
    cache: TtlCache[str, int] = TtlCache(10, clock)
    cache.set("a", 1)
    clock.now += 8
    cache.set("a", 2)
    clock.now += 8
    assert cache.get("a") == 2


def test_ttl_cache_evicts_oldest() -> None:
    cache: TtlCache[str, int] = TtlCache(10, FakeClock(), max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_ttl_cache_clear() -> None:
    cache: TtlCache[str, int] = TtlCache(10, FakeClock())
    cache.set("a", 1)
    cache.clear()
    assert cache.get("a") is None


def test_cached_provider_is_a_stats_provider() -> None:
    provider: StatsProvider = CachedStatsProvider(FakeStats())
    assert provider is not None


async def test_profile_cache_hit() -> None:
    inner = FakeStats()
    provider = CachedStatsProvider(inner, ttl_seconds=300, clock=FakeClock())
    first = await provider.get_profile(STEAM_A)
    second = await provider.get_profile(STEAM_A)
    assert first is second
    assert inner.profile_calls == [STEAM_A]


async def test_profile_cache_expiry_refetches() -> None:
    clock = FakeClock()
    inner = FakeStats()
    provider = CachedStatsProvider(inner, ttl_seconds=300, clock=clock)
    await provider.get_profile(STEAM_A)
    clock.now += 300
    profile = await provider.get_profile(STEAM_A)
    assert profile.name == "call2"
    assert inner.profile_calls == [STEAM_A, STEAM_A]


async def test_profile_cache_keys_do_not_collide() -> None:
    inner = FakeStats()
    provider = CachedStatsProvider(inner, clock=FakeClock())
    a = await provider.get_profile(STEAM_A)
    b = await provider.get_profile(STEAM_B)
    assert a.steam64_id == STEAM_A
    assert b.steam64_id == STEAM_B
    assert (await provider.get_profile(STEAM_A)) is a
    assert (await provider.get_profile(STEAM_B)) is b
    assert inner.profile_calls == [STEAM_A, STEAM_B]


async def test_profile_errors_are_not_cached() -> None:
    inner = FakeStats()
    inner.error = ApiError()
    provider = CachedStatsProvider(inner, clock=FakeClock())
    with pytest.raises(ApiError):
        await provider.get_profile(STEAM_A)
    inner.error = None
    profile = await provider.get_profile(STEAM_A)
    assert profile.steam64_id == STEAM_A
    assert inner.profile_calls == [STEAM_A, STEAM_A]


async def test_matches_pass_through_uncached() -> None:
    inner = FakeStats()
    provider = CachedStatsProvider(inner, clock=FakeClock())
    first = await provider.get_recent_matches(STEAM_A)
    second = await provider.get_recent_matches(STEAM_A)
    assert [m.id for m in first] == ["m1"]
    assert [m.id for m in second] == ["m2"]
    assert inner.matches_calls == [STEAM_A, STEAM_A]

    await provider.get_match("g1")
    match = await provider.get_match("g1")
    assert match.id == "g1"
    assert inner.match_calls == ["g1", "g1"]


async def test_aclose_delegates() -> None:
    inner = FakeStats()
    provider = CachedStatsProvider(inner)
    await provider.aclose()
    assert inner.closed == 1
