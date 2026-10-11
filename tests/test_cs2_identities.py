import asyncio
import logging
from collections.abc import Collection
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ninja_bot.cogs.cs2 import (
    MEMBER_MISSING_TTL_SECONDS,
    MEMBER_NAME_TTL_SECONDS,
    Cs2,
    MemberCache,
    member_lookup,
)
from ninja_bot.cs2.identities import MAX_LOOKUPS, Identity, resolve_identities
from ninja_bot.cs2.models import Match, PlayerMatchStats

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
STEAM_C = "76561198000000003"


class FakeLinks:
    """Only the reverse lookup; records every query."""

    def __init__(self, links: dict[str, int] | None = None) -> None:
        self.links = dict(links or {})
        self.queries: list[list[str]] = []

    async def get(self, discord_id: int) -> str | None:
        raise AssertionError("not used")

    async def set(self, discord_id: int, steam64_id: str) -> None:
        raise AssertionError("not used")

    async def remove(self, discord_id: int) -> bool:
        raise AssertionError("not used")

    async def all_links(self) -> dict[int, str]:
        raise AssertionError("not used")

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        self.queries.append(list(steam64_ids))
        return {s: self.links[s] for s in steam64_ids if s in self.links}


class FakeLookup:
    def __init__(
        self, names: dict[int, str] | None = None, errors: dict[int, Exception] | None = None
    ) -> None:
        self.names = dict(names or {})
        self.errors = dict(errors or {})
        self.calls: list[int] = []

    async def __call__(self, discord_id: int) -> str | None:
        self.calls.append(discord_id)
        if discord_id in self.errors:
            raise self.errors[discord_id]
        return self.names.get(discord_id)


def players(*steam64_ids: str) -> list[PlayerMatchStats]:
    return [PlayerMatchStats(steam64_id=s) for s in steam64_ids]


async def test_linked_member_found() -> None:
    lookup = FakeLookup({1: "Alice"})
    result = await resolve_identities(players(STEAM_A), FakeLinks({STEAM_A: 1}), lookup)
    assert result == {STEAM_A: Identity(STEAM_A, 1, "Alice")}


async def test_linked_but_not_in_server() -> None:
    lookup = FakeLookup()
    result = await resolve_identities(players(STEAM_A), FakeLinks({STEAM_A: 1}), lookup)
    assert result == {}
    assert lookup.calls == [1]


async def test_unlinked_players_are_not_looked_up() -> None:
    lookup = FakeLookup({1: "Alice"})
    links = FakeLinks({STEAM_A: 1})
    result = await resolve_identities(players(STEAM_A, STEAM_B), links, lookup)
    assert set(result) == {STEAM_A}
    assert lookup.calls == [1]


async def test_same_discord_user_on_two_steam_ids_is_looked_up_once() -> None:
    lookup = FakeLookup({1: "Alice"})
    links = FakeLinks({STEAM_A: 1, STEAM_B: 1})
    result = await resolve_identities(players(STEAM_A, STEAM_B), links, lookup)
    assert result == {
        STEAM_A: Identity(STEAM_A, 1, "Alice"),
        STEAM_B: Identity(STEAM_B, 1, "Alice"),
    }
    assert lookup.calls == [1]


async def test_lookup_raising_counts_as_not_member(caplog: pytest.LogCaptureFixture) -> None:
    lookup = FakeLookup({2: "Bob"}, errors={1: RuntimeError("secret detail")})
    links = FakeLinks({STEAM_A: 1, STEAM_B: 2})
    with caplog.at_level(logging.WARNING):
        result = await resolve_identities(players(STEAM_A, STEAM_B), links, lookup)
    assert result == {STEAM_B: Identity(STEAM_B, 2, "Bob")}
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_lookups_run_concurrently() -> None:
    started: list[int] = []
    gate = asyncio.Event()

    async def lookup(discord_id: int) -> str | None:
        started.append(discord_id)
        await gate.wait()
        return f"user{discord_id}"

    links = FakeLinks({STEAM_A: 1, STEAM_B: 2, STEAM_C: 3})
    task = asyncio.create_task(
        resolve_identities(players(STEAM_A, STEAM_B, STEAM_C), links, lookup)
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert sorted(started) == [1, 2, 3]
    assert not task.done()
    gate.set()
    assert len(await task) == 3


async def test_empty_players_skip_the_store() -> None:
    links = FakeLinks({STEAM_A: 1})
    lookup = FakeLookup({1: "Alice"})
    assert await resolve_identities([], links, lookup) == {}
    assert links.queries == []
    assert lookup.calls == []


async def test_duplicate_players_are_queried_once() -> None:
    links = FakeLinks({STEAM_A: 1})
    await resolve_identities(players(STEAM_A, STEAM_A), links, FakeLookup({1: "Alice"}))
    assert links.queries == [[STEAM_A]]


async def test_lookups_are_capped() -> None:
    ids = [f"7656119800000{n:04d}" for n in range(MAX_LOOKUPS + 5)]
    links = FakeLinks({s: n for n, s in enumerate(ids)})
    lookup = FakeLookup({n: f"user{n}" for n in range(len(ids))})
    result = await resolve_identities(players(*ids), links, lookup)
    assert len(lookup.calls) == MAX_LOOKUPS
    assert len(result) == MAX_LOOKUPS


# --- cog lookup ---


def make_member(name: str) -> MagicMock:
    member = MagicMock()
    member.display_name = name
    return member


def http_error(status: int) -> discord.HTTPException:
    response = MagicMock()
    response.status = status
    response.reason = "error"
    if status == 404:
        return discord.NotFound(response, "Unknown Member")
    return discord.HTTPException(response, "boom")


async def test_cog_lookup_cache_hit() -> None:
    guild = MagicMock()
    guild.get_member.return_value = make_member("Cached")
    guild.fetch_member = AsyncMock()
    assert await member_lookup(guild, MemberCache())(1) == "Cached"
    guild.fetch_member.assert_not_awaited()


async def test_cog_lookup_fetch_fallback() -> None:
    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=make_member("Fetched"))
    assert await member_lookup(guild, MemberCache())(1) == "Fetched"
    guild.fetch_member.assert_awaited_once_with(1)


async def test_cog_lookup_not_found() -> None:
    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=http_error(404))
    assert await member_lookup(guild, MemberCache())(1) is None


async def test_cog_lookup_http_error(caplog: pytest.LogCaptureFixture) -> None:
    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=http_error(500))
    with caplog.at_level(logging.WARNING):
        assert await member_lookup(guild, MemberCache())(1) is None
    assert "500" in caplog.text


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_guild(guild_id: int = 10, member: MagicMock | None = None) -> MagicMock:
    """Guild whose member cache is empty unless `member` is given."""
    guild = MagicMock()
    guild.id = guild_id
    guild.get_member.return_value = member
    guild.fetch_member = AsyncMock(return_value=make_member("Fetched"))
    return guild


async def test_cached_lookup_makes_no_discord_calls() -> None:
    guild = make_guild()
    lookup = member_lookup(guild, MemberCache(FakeClock()))
    assert await lookup(1) == "Fetched"
    guild.get_member.reset_mock()
    assert await lookup(1) == "Fetched"
    guild.get_member.assert_not_called()
    guild.fetch_member.assert_awaited_once_with(1)


async def test_member_cache_hit_is_cached() -> None:
    guild = make_guild(member=make_member("Cached"))
    lookup = member_lookup(guild, MemberCache(FakeClock()))
    assert await lookup(1) == "Cached"
    assert await lookup(1) == "Cached"
    guild.get_member.assert_called_once_with(1)
    guild.fetch_member.assert_not_awaited()


async def test_not_found_is_cached_then_retried() -> None:
    clock = FakeClock()
    guild = make_guild()
    guild.fetch_member.side_effect = http_error(404)
    lookup = member_lookup(guild, MemberCache(clock))
    assert await lookup(1) is None
    clock.now += MEMBER_MISSING_TTL_SECONDS - 1
    assert await lookup(1) is None
    assert guild.fetch_member.await_count == 1
    clock.now += 1
    guild.fetch_member.side_effect = None
    assert await lookup(1) == "Fetched"
    assert guild.fetch_member.await_count == 2


async def test_http_error_is_not_cached(caplog: pytest.LogCaptureFixture) -> None:
    guild = make_guild()
    guild.fetch_member.side_effect = http_error(429)
    lookup = member_lookup(guild, MemberCache(FakeClock()))
    with caplog.at_level(logging.WARNING):
        assert await lookup(1) is None
    assert all(record.exc_info is None for record in caplog.records)
    guild.fetch_member.side_effect = None
    assert await lookup(1) == "Fetched"
    assert guild.fetch_member.await_count == 2


async def test_forbidden_is_not_cached_as_stranger() -> None:
    guild = make_guild()
    response = MagicMock(status=403, reason="Forbidden")
    guild.fetch_member.side_effect = discord.Forbidden(response, "no")
    cache = MemberCache(FakeClock())
    assert await member_lookup(guild, cache)(1) is None
    assert cache.not_members.get((10, 1)) is None


async def test_names_expire_after_ttl() -> None:
    clock = FakeClock()
    guild = make_guild()
    lookup = member_lookup(guild, MemberCache(clock))
    await lookup(1)
    clock.now += MEMBER_NAME_TTL_SECONDS - 1
    await lookup(1)
    assert guild.fetch_member.await_count == 1
    clock.now += 1
    guild.fetch_member.return_value = make_member("Renamed")
    assert await lookup(1) == "Renamed"
    assert guild.fetch_member.await_count == 2


async def test_guilds_do_not_collide() -> None:
    cache = MemberCache(FakeClock())
    first = make_guild(10, make_member("In first"))
    second = make_guild(20)
    second.fetch_member.side_effect = http_error(404)
    assert await member_lookup(first, cache)(1) == "In first"
    assert await member_lookup(second, cache)(1) is None
    assert await member_lookup(first, cache)(1) == "In first"
    second.fetch_member.assert_awaited_once_with(1)


async def test_concurrent_lookups_share_rest_calls() -> None:
    gate = asyncio.Event()
    guild = make_guild()

    async def fetch_member(discord_id: int) -> MagicMock:
        await gate.wait()
        return make_member(f"User {discord_id}")

    guild.fetch_member = AsyncMock(side_effect=fetch_member)
    lookup = member_lookup(guild, MemberCache(FakeClock()))
    tasks = [asyncio.create_task(lookup(i % 5)) for i in range(10)]
    await asyncio.sleep(0)
    gate.set()
    names = await asyncio.gather(*tasks)
    assert names == [f"User {i % 5}" for i in range(10)]
    assert guild.fetch_member.await_count == 5


async def test_cog_keeps_one_member_cache() -> None:
    cog = Cs2(MagicMock(), FakeLinks({STEAM_A: 1}))
    guild = make_guild()
    await cog._match_identities(guild, make_match(STEAM_A))
    await cog._match_identities(guild, make_match(STEAM_A))
    guild.fetch_member.assert_awaited_once_with(1)


def make_match(*steam64_ids: str) -> Match:
    return Match(
        id="m1",
        map_name=None,
        finished_at=None,
        team_scores=(),
        players=tuple(players(*steam64_ids)),
    )


async def test_cog_identities_without_guild_skip_the_store() -> None:
    links = FakeLinks({STEAM_A: 1})
    cog = Cs2(MagicMock(), links)
    interaction = MagicMock()
    interaction.guild = None
    assert await cog._match_identities(interaction.guild, make_match(STEAM_A)) == {}
    assert links.queries == []


async def test_cog_identities_in_guild() -> None:
    links = FakeLinks({STEAM_A: 1})
    cog = Cs2(MagicMock(), links)
    interaction = MagicMock()
    interaction.guild.get_member.return_value = make_member("Alice")
    result = await cog._match_identities(interaction.guild, make_match(STEAM_A, STEAM_B))
    assert result == {STEAM_A: Identity(STEAM_A, 1, "Alice")}


class BrokenLinks(FakeLinks):
    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        raise RuntimeError("db down")


async def test_cog_identities_or_none_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    cog = Cs2(MagicMock(), BrokenLinks())
    assert await cog._match_identities_or_none(make_guild(), make_match(STEAM_A)) is None
    assert "Posting scoreboard without Discord names" in caplog.text


async def test_cog_identities_or_none_returns_dict() -> None:
    cog = Cs2(MagicMock(), FakeLinks({STEAM_A: 1}))
    guild = make_guild()
    assert await cog._match_identities_or_none(guild, make_match(STEAM_B)) == {}
    result = await cog._match_identities_or_none(guild, make_match(STEAM_A))
    assert result == {STEAM_A: Identity(STEAM_A, 1, "Fetched")}
