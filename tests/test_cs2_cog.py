import asyncio
import dataclasses
import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from ninja_bot.bot import EXTENSIONS
from ninja_bot.cogs import cs2 as cs2_cog
from ninja_bot.cogs.cs2 import (
    ALL_PROFILES_FAILED_MESSAGE,
    ALREADY_LINKED_MESSAGE,
    COOLDOWN_SECONDS,
    GENERIC_ERROR_MESSAGE,
    INVALID_MATCH_ID_MESSAGE,
    LEADERBOARD_COOLDOWN_SECONDS,
    LINK_COOLDOWN_SECONDS,
    MAX_LEADERBOARD_LINKS,
    MAX_MATCH_ID_LENGTH,
    NOTHING_TO_UNLINK_MESSAGE,
    UNLINKED_MESSAGE,
    Cs2,
    clean_match_id,
    cooldown_message,
    linked_message,
    not_linked_message,
)
from ninja_bot.config import load_config
from ninja_bot.cs2.awards import compute_awards
from ninja_bot.cs2.cache import CachedStatsProvider
from ninja_bot.cs2.client import FetchResponse, LeetifyClient
from ninja_bot.cs2.embed import (
    BREAKDOWN_FIELD,
    FRIENDS_FIELD,
    LEADERBOARD_FIELD,
    NOBODY_LINKED_MESSAGE,
    RATINGS_FIELD,
    TEAM_NAMES,
    YOUR_TEAM,
    build_match_embed,
)
from ninja_bot.cs2.identities import Identity
from ninja_bot.cs2.models import (
    ApiError,
    Cs2Error,
    InvalidSteamIdError,
    Match,
    MatchNotFoundError,
    NoMatchesError,
    NotLinkedError,
    PlayerNotFoundError,
    PrivateProfileError,
    Profile,
    RateLimitedError,
    SteamLookupError,
    VanityNotFoundError,
    parse_match,
    parse_profile,
)
from ninja_bot.cs2.resolver import SteamCommunityResolver
from ninja_bot.cs2.squad import Metric, metric_label
from ninja_bot.cs2.steam import MAX_STEAM_INPUT_LENGTH
from ninja_bot.cs2.store import SqliteLinkStore
from ninja_bot.media.cooldown import UserCooldown

STEAM_ID = "76561198000000001"
USER_ID = 42


def make_profile(steam64_id: str = STEAM_ID, name: str | None = "Ninja") -> Profile:
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


class FakeStats:
    def __init__(
        self,
        profile: Profile | None = None,
        error: Exception | None = None,
        matches: list[Match] | None = None,
        match_error: Exception | None = None,
        by_id: list[Match] | None = None,
    ) -> None:
        self.profile = profile or make_profile()
        # `get_match` looks in `by_id` first, so history entries can differ from it.
        self.by_id = by_id if by_id is not None else []
        self.error = error
        self.matches = matches if matches is not None else []
        self.match_error = match_error
        self.profile_calls: list[str] = []
        self.recent_calls: list[str] = []
        self.match_calls: list[str] = []
        self.close_calls = 0

    async def get_profile(self, steam64_id: str) -> Profile:
        self.profile_calls.append(steam64_id)
        if self.error is not None:
            raise self.error
        return self.profile

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        self.recent_calls.append(steam64_id)
        if self.match_error is not None:
            raise self.match_error
        return list(self.matches)

    async def get_match(self, game_id: str) -> Match:
        self.match_calls.append(game_id)
        if self.match_error is not None:
            raise self.match_error
        return next(m for m in [*self.by_id, *self.matches] if m.id == game_id)

    async def aclose(self) -> None:
        self.close_calls += 1


class MemoryLinkStore:
    def __init__(self, links: dict[int, str] | None = None) -> None:
        self.links = dict(links or {})

    async def get(self, discord_id: int) -> str | None:
        return self.links.get(discord_id)

    async def set(self, discord_id: int, steam64_id: str) -> None:
        self.links[discord_id] = steam64_id

    async def remove(self, discord_id: int) -> bool:
        return self.links.pop(discord_id, None) is not None

    async def all_links(self) -> dict[int, str]:
        return dict(self.links)

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        wanted = set(steam64_ids)
        result: dict[str, int] = {}
        for discord_id, steam64_id in sorted(self.links.items()):
            if steam64_id in wanted:
                result.setdefault(steam64_id, discord_id)
        return result


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_interaction(user_id: int = USER_ID) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Caller"
    # No server by default; scoreboard tests that need members attach a `FakeGuild`.
    interaction.guild = None
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def reply(interaction: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    return args, kwargs


def followup_reply(interaction: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    return args, kwargs


def make_cog(
    stats: FakeStats | None = None,
    store: MemoryLinkStore | None = None,
    cooldown: UserCooldown | None = None,
) -> tuple[Cs2, FakeStats, MemoryLinkStore]:
    stats = stats or FakeStats()
    store = store if store is not None else MemoryLinkStore()
    cooldown = cooldown or UserCooldown(COOLDOWN_SECONDS)
    return Cs2(stats, store, cooldown), stats, store


async def test_link_success_stores_and_replies_ephemeral() -> None:
    cog, stats, store = make_cog()
    interaction = make_interaction()
    await Cs2.link.callback(cog, interaction, STEAM_ID)
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {USER_ID: STEAM_ID}
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.response.send_message.assert_not_awaited()
    args, kwargs = followup_reply(interaction)
    assert args == (linked_message("Ninja"),)
    assert kwargs == {"ephemeral": True}
    assert "Ninja" in args[0]


async def test_link_defers_before_profile_lookup() -> None:
    calls: list[str] = []
    stats = FakeStats()
    original = stats.get_profile

    async def get_profile(steam64_id: str) -> Profile:
        calls.append("get_profile")
        return await original(steam64_id)

    stats.get_profile = get_profile  # type: ignore[method-assign]
    cog, _, _ = make_cog(stats)
    interaction = make_interaction()
    interaction.response.defer = AsyncMock(side_effect=lambda **_: calls.append("defer"))
    await cog.handle_link(interaction, STEAM_ID)
    assert calls == ["defer", "get_profile"]


async def test_link_followup_failure_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    cog, _, store = make_cog()
    interaction = make_interaction()
    response = MagicMock(status=404, reason="Not Found")
    interaction.followup.send = AsyncMock(side_effect=discord.NotFound(response, "expired"))
    await cog.handle_link(interaction, STEAM_ID)
    assert store.links == {USER_ID: STEAM_ID}
    assert "Could not send CS2 response" in caplog.text


async def test_link_accepts_profile_url() -> None:
    cog, _, store = make_cog()
    await cog.handle_link(make_interaction(), f"https://steamcommunity.com/profiles/{STEAM_ID}/")
    assert store.links == {USER_ID: STEAM_ID}


async def test_link_replaces_existing_link() -> None:
    cog, _, store = make_cog(store=MemoryLinkStore({USER_ID: "76561198000000999"}))
    await cog.handle_link(make_interaction(), STEAM_ID)
    assert store.links == {USER_ID: STEAM_ID}


async def test_link_without_name_falls_back_to_steam_id() -> None:
    cog, _, _ = make_cog(FakeStats(make_profile(name=None)))
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    args, _ = followup_reply(interaction)
    assert STEAM_ID in args[0]


def test_linked_message_escapes_markdown() -> None:
    assert "\\*" in linked_message("a*b")
    assert linked_message("[x](https://evil.example)") == (
        "Linked to **\\[x\\]\\(https://evil.example\\)** on Leetify."
    )


def test_not_linked_message_escapes_links() -> None:
    assert "\\[x\\]\\(" in not_linked_message("[x](https://evil.example)")


@pytest.mark.parametrize(
    ("steam", "message"),
    [
        ("not an id", InvalidSteamIdError.default_message),
        ("evil.example/id/gaben", InvalidSteamIdError.default_message),
    ],
)
async def test_link_invalid_id_is_ephemeral_and_skips_api(steam: str, message: str) -> None:
    cog, stats, store = make_cog()
    interaction = make_interaction()
    await cog.handle_link(interaction, steam)
    assert stats.profile_calls == []
    assert store.links == {}
    assert reply(interaction) == ((message,), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [PlayerNotFoundError(), PrivateProfileError(), RateLimitedError(), ApiError()],
    ids=type,
)
async def test_link_api_errors_are_ephemeral_and_not_stored(error: Cs2Error) -> None:
    cog, _, store = make_cog(FakeStats(error=error))
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert store.links == {}
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert followup_reply(interaction) == ((error.message,), {"ephemeral": True})


async def test_link_unexpected_error_is_generic_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cog, _, store = make_cog(FakeStats(error=RuntimeError("boom")))
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert store.links == {}
    assert followup_reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error linking" in caplog.text


def link_cog(
    links: dict[int, str] | None = None, **stats_kwargs: Any
) -> tuple[Cs2, FakeStats, MemoryLinkStore, FakeClock]:
    clock = FakeClock()
    stats = FakeStats(**stats_kwargs)
    store = MemoryLinkStore(links)
    cog = Cs2(
        stats,
        store,
        UserCooldown(COOLDOWN_SECONDS, clock),
        UserCooldown(LINK_COOLDOWN_SECONDS, clock),
    )
    return cog, stats, store, clock


async def test_link_invalid_input_does_not_take_cooldown() -> None:
    cog, stats, store, _ = link_cog()
    await cog.handle_link(make_interaction(), "not an id")
    await cog.handle_link(make_interaction(), STEAM_ID)
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {USER_ID: STEAM_ID}


async def test_link_repeat_inside_window_is_throttled() -> None:
    cog, stats, store, clock = link_cog()
    await cog.handle_link(make_interaction(), STEAM_ID)
    await cog.links.remove(USER_ID)
    clock.now += LINK_COOLDOWN_SECONDS - 5
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {}
    assert reply(interaction) == ((cooldown_message(5),), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()


async def test_link_after_window_works() -> None:
    cog, stats, store, clock = link_cog()
    other = "76561198000000999"
    await cog.handle_link(make_interaction(), other)
    clock.now += LINK_COOLDOWN_SECONDS
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert stats.profile_calls == [other, STEAM_ID]
    assert store.links == {USER_ID: STEAM_ID}
    assert followup_reply(interaction) == ((linked_message("Ninja"),), {"ephemeral": True})


async def test_link_already_linked_same_id_skips_api_and_cooldown() -> None:
    cog, stats, store, _ = link_cog({USER_ID: STEAM_ID})
    interaction = make_interaction()
    await cog.handle_link(interaction, f"https://steamcommunity.com/profiles/{STEAM_ID}")
    assert stats.profile_calls == []
    assert reply(interaction) == ((ALREADY_LINKED_MESSAGE,), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()
    # No cooldown was taken, so relinking another id right away works.
    other = "76561198000000999"
    await cog.handle_link(make_interaction(), other)
    assert stats.profile_calls == [other]
    assert store.links == {USER_ID: other}


async def test_link_relinks_different_id() -> None:
    cog, stats, store, _ = link_cog({USER_ID: "76561198000000999"})
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {USER_ID: STEAM_ID}
    assert followup_reply(interaction) == ((linked_message("Ninja"),), {"ephemeral": True})


async def test_link_failed_lookup_holds_cooldown() -> None:
    cog, stats, store, _ = link_cog(error=PlayerNotFoundError())
    await cog.handle_link(make_interaction(), STEAM_ID)
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {}
    assert reply(interaction) == (
        (cooldown_message(LINK_COOLDOWN_SECONDS),),
        {"ephemeral": True},
    )


async def test_link_cooldown_is_separate_from_stats_cooldown() -> None:
    cog, _, _, _ = link_cog()
    await cog.handle_link(make_interaction(), STEAM_ID)
    assert cog.cooldown.try_acquire(USER_ID) == 0.0
    assert cog.link_cooldown.try_acquire(USER_ID) > 0


async def test_link_store_lookup_error_is_generic(caplog: pytest.LogCaptureFixture) -> None:
    cog, stats, store, _ = link_cog()
    store.get = AsyncMock(side_effect=RuntimeError("disk"))  # type: ignore[method-assign]
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert stats.profile_calls == []
    assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    # The cooldown was not taken.
    assert cog.link_cooldown.try_acquire(USER_ID) == 0.0


VANITY_NAME = "ninja_42"


class FakeResolver:
    def __init__(self, steam64_id: str = STEAM_ID, error: Exception | None = None) -> None:
        self.steam64_id = steam64_id
        self.error = error
        self.calls: list[str] = []
        self.close_calls = 0

    async def resolve_vanity(self, name: str) -> str:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return self.steam64_id

    async def aclose(self) -> None:
        self.close_calls += 1


def vanity_cog(
    links: dict[int, str] | None = None,
    resolver: FakeResolver | None = None,
    **stats_kwargs: Any,
) -> tuple[Cs2, FakeStats, MemoryLinkStore, FakeResolver, FakeClock]:
    clock = FakeClock()
    stats = FakeStats(**stats_kwargs)
    store = MemoryLinkStore(links)
    resolver = resolver or FakeResolver()
    cog = Cs2(
        stats,
        store,
        UserCooldown(COOLDOWN_SECONDS, clock),
        UserCooldown(LINK_COOLDOWN_SECONDS, clock),
        resolver,
    )
    return cog, stats, store, resolver, clock


@pytest.mark.parametrize(
    "steam",
    [
        VANITY_NAME,
        f"https://steamcommunity.com/id/{VANITY_NAME}/",
        f"steamcommunity.com/id/{VANITY_NAME}?l=english",
    ],
)
async def test_link_vanity_resolves_and_links(steam: str) -> None:
    cog, stats, store, resolver, _ = vanity_cog()
    interaction = make_interaction()
    await Cs2.link.callback(cog, interaction, steam)
    assert resolver.calls == [VANITY_NAME]
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {USER_ID: STEAM_ID}
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.response.send_message.assert_not_awaited()
    args, kwargs = followup_reply(interaction)
    assert args == (linked_message("Ninja", STEAM_ID),)
    assert kwargs == {"ephemeral": True}
    assert "Ninja" in args[0]
    assert STEAM_ID in args[0]


def test_linked_message_shows_resolved_id() -> None:
    assert linked_message("Ninja", STEAM_ID) == (
        f"Linked to **Ninja** on Leetify (Steam ID {STEAM_ID})."
    )
    assert STEAM_ID not in linked_message("Ninja")


@pytest.mark.parametrize("steam", [STEAM_ID, f"https://steamcommunity.com/profiles/{STEAM_ID}"])
async def test_link_steam_id_makes_no_resolver_call(steam: str) -> None:
    cog, _, store, resolver, _ = vanity_cog()
    interaction = make_interaction()
    await cog.handle_link(interaction, steam)
    assert resolver.calls == []
    assert store.links == {USER_ID: STEAM_ID}
    assert followup_reply(interaction) == ((linked_message("Ninja"),), {"ephemeral": True})


async def test_link_already_linked_steam_id_makes_no_resolver_call() -> None:
    cog, _, _, resolver, _ = vanity_cog({USER_ID: STEAM_ID})
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert resolver.calls == []
    assert reply(interaction) == ((ALREADY_LINKED_MESSAGE,), {"ephemeral": True})


async def test_link_vanity_already_linked_replies_through_followup() -> None:
    cog, stats, store, resolver, _ = vanity_cog({USER_ID: STEAM_ID})
    interaction = make_interaction()
    await cog.handle_link(interaction, VANITY_NAME)
    assert resolver.calls == [VANITY_NAME]
    assert stats.profile_calls == []
    assert store.links == {USER_ID: STEAM_ID}
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.response.send_message.assert_not_awaited()
    assert followup_reply(interaction) == ((ALREADY_LINKED_MESSAGE,), {"ephemeral": True})


async def test_link_vanity_relinks_different_id() -> None:
    cog, _, store, _, _ = vanity_cog({USER_ID: "76561198000000999"})
    await cog.handle_link(make_interaction(), VANITY_NAME)
    assert store.links == {USER_ID: STEAM_ID}


@pytest.mark.parametrize(
    "error",
    [VanityNotFoundError(), SteamLookupError(), RateLimitedError("Steam is busy"), Cs2Error("x")],
    ids=type,
)
async def test_link_vanity_resolver_errors_are_ephemeral(error: Cs2Error) -> None:
    cog, stats, store, _, _ = vanity_cog(resolver=FakeResolver(error=error))
    interaction = make_interaction()
    await cog.handle_link(interaction, VANITY_NAME)
    assert stats.profile_calls == []
    assert store.links == {}
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert followup_reply(interaction) == ((error.message,), {"ephemeral": True})


async def test_link_vanity_unexpected_resolver_error_is_generic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cog, _, store, _, _ = vanity_cog(resolver=FakeResolver(error=RuntimeError("boom")))
    interaction = make_interaction()
    await cog.handle_link(interaction, VANITY_NAME)
    assert store.links == {}
    assert followup_reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error linking" in caplog.text


async def test_link_vanity_without_leetify_profile() -> None:
    cog, stats, store, resolver, _ = vanity_cog(error=PlayerNotFoundError())
    interaction = make_interaction()
    await cog.handle_link(interaction, VANITY_NAME)
    assert resolver.calls == [VANITY_NAME]
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {}
    assert followup_reply(interaction) == (
        (PlayerNotFoundError.default_message,),
        {"ephemeral": True},
    )


async def test_link_vanity_store_error_after_defer_is_generic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cog, stats, store, _, _ = vanity_cog()
    store.get = AsyncMock(side_effect=RuntimeError("disk"))  # type: ignore[method-assign]
    interaction = make_interaction()
    await cog.handle_link(interaction, VANITY_NAME)
    assert stats.profile_calls == []
    assert followup_reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error linking" in caplog.text


async def test_link_vanity_cooldown_taken_once_and_not_refunded() -> None:
    cog, _, store, resolver, clock = vanity_cog(resolver=FakeResolver(error=VanityNotFoundError()))
    await cog.handle_link(make_interaction(), VANITY_NAME)
    clock.now += LINK_COOLDOWN_SECONDS - 5
    interaction = make_interaction()
    await cog.handle_link(interaction, VANITY_NAME)
    assert resolver.calls == [VANITY_NAME]
    assert store.links == {}
    assert reply(interaction) == ((cooldown_message(5),), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()
    # A successful vanity link holds the cooldown too.
    clock.now += 5
    resolver.error = None
    await cog.handle_link(make_interaction(), VANITY_NAME)
    assert store.links == {USER_ID: STEAM_ID}
    assert cog.link_cooldown.try_acquire(USER_ID) == LINK_COOLDOWN_SECONDS


async def test_link_vanity_already_linked_holds_cooldown() -> None:
    cog, _, _, _, _ = vanity_cog({USER_ID: STEAM_ID})
    await cog.handle_link(make_interaction(), VANITY_NAME)
    assert cog.link_cooldown.try_acquire(USER_ID) == LINK_COOLDOWN_SECONDS


async def test_link_vanity_invalid_input_makes_no_resolver_call() -> None:
    cog, _, _, resolver, _ = vanity_cog()
    interaction = make_interaction()
    await cog.handle_link(interaction, "no spaces allowed")
    assert resolver.calls == []
    assert reply(interaction) == ((InvalidSteamIdError.default_message,), {"ephemeral": True})
    assert cog.link_cooldown.try_acquire(USER_ID) == 0.0


async def test_link_vanity_defers_before_resolver_call() -> None:
    calls: list[str] = []
    resolver = FakeResolver()
    original = resolver.resolve_vanity

    async def resolve_vanity(name: str) -> str:
        calls.append("resolve")
        return await original(name)

    resolver.resolve_vanity = resolve_vanity  # type: ignore[method-assign]
    cog, _, _, _, _ = vanity_cog(resolver=resolver)
    interaction = make_interaction()
    interaction.response.defer = AsyncMock(side_effect=lambda **_: calls.append("defer"))
    await cog.handle_link(interaction, VANITY_NAME)
    assert calls == ["defer", "resolve"]


async def test_unlink_removes_existing_link() -> None:
    cog, _, store = make_cog(store=MemoryLinkStore({USER_ID: STEAM_ID, 7: STEAM_ID}))
    interaction = make_interaction()
    await Cs2.unlink.callback(cog, interaction)
    assert store.links == {7: STEAM_ID}
    assert reply(interaction) == ((UNLINKED_MESSAGE,), {"ephemeral": True})


async def test_unlink_without_link() -> None:
    cog, _, _ = make_cog()
    interaction = make_interaction()
    await cog.handle_unlink(interaction)
    assert reply(interaction) == ((NOTHING_TO_UNLINK_MESSAGE,), {"ephemeral": True})


async def test_unlink_unexpected_error_is_generic(caplog: pytest.LogCaptureFixture) -> None:
    store = MemoryLinkStore()
    store.remove = AsyncMock(side_effect=RuntimeError("disk"))  # type: ignore[method-assign]
    cog, _, _ = make_cog(store=store)
    interaction = make_interaction()
    await cog.handle_unlink(interaction)
    assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error unlinking" in caplog.text


def test_link_steam_option_metadata() -> None:
    param = Cs2.link._params["steam"]
    assert param.required
    assert str(param.description)
    assert param.to_dict()["max_length"] == MAX_STEAM_INPUT_LENGTH == 200


def test_unlink_has_no_options() -> None:
    assert Cs2.unlink._params == {}


@pytest.mark.parametrize("done", [False, True])
async def test_cog_app_command_error_is_generic(
    done: bool, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, _ = make_cog()
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=done)
    await cog.cog_app_command_error(interaction, app_commands.AppCommandError("x"))
    send = interaction.followup.send if done else interaction.response.send_message
    send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE, ephemeral=True)
    assert "App command error in Cs2 cog" in caplog.text


async def test_cog_app_command_error_uses_followup_after_link_defer() -> None:
    cog, _, _ = make_cog()
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=False)

    async def defer(**_: Any) -> None:
        interaction.response.is_done.return_value = True

    interaction.response.defer = AsyncMock(side_effect=defer)
    await interaction.response.defer(ephemeral=True, thinking=True)
    await cog.cog_app_command_error(interaction, app_commands.AppCommandError("x"))
    interaction.followup.send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE, ephemeral=True)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.parametrize("done", [False, True])
async def test_cog_app_command_error_send_failure_is_swallowed(
    done: bool, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, _ = make_cog()
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=done)
    error = discord.HTTPException(MagicMock(status=500, reason="boom"), "boom")
    interaction.followup.send = AsyncMock(side_effect=error)
    interaction.response.send_message = AsyncMock(side_effect=error)
    await cog.cog_app_command_error(interaction, app_commands.AppCommandError("x"))
    assert "Could not send CS2 response" in caplog.text


def test_extension_registered() -> None:
    assert "ninja_bot.cogs.cs2" in EXTENSIONS


async def test_setup_registers_cog(tmp_path: Path) -> None:
    bot = MagicMock()
    bot.config = load_config(
        {"DISCORD_TOKEN": "x", "GUILD_ID": "1", "LEETIFY_API_KEY": "key", "DATA_DIR": str(tmp_path)}
    )
    bot.add_cog = AsyncMock()
    await cs2_cog.setup(bot)
    bot.add_cog.assert_awaited_once()
    cog = bot.add_cog.call_args.args[0]
    assert isinstance(cog, Cs2)
    assert isinstance(cog.stats, CachedStatsProvider)
    assert isinstance(cog.stats._inner, LeetifyClient)
    assert cog.stats._inner._api_key == "key"
    assert isinstance(cog.links, SqliteLinkStore)
    assert cog.links._path == tmp_path / "cs2.sqlite3"
    assert cog.cooldown.per_seconds == COOLDOWN_SECONDS == 10.0
    assert cog.link_cooldown.per_seconds == LINK_COOLDOWN_SECONDS == 15
    assert cog.link_cooldown is not cog.cooldown
    assert isinstance(cog.resolver, SteamCommunityResolver)
    assert cog.leaderboard_cooldown.per_seconds == LEADERBOARD_COOLDOWN_SECONDS == 30
    assert cog.leaderboard_cooldown is not cog.cooldown
    assert cog.auto_post is not None
    assert cog.auto_post.bot is bot
    assert cog.auto_post.channel_id is None
    assert cog.auto_post.poll_seconds == 300
    assert cog.auto_post.poller._provider is cog.stats
    assert cog.auto_post.poller._store is cog.links


async def test_setup_passes_channel_config(tmp_path: Path) -> None:
    bot = MagicMock()
    bot.config = load_config(
        {
            "DISCORD_TOKEN": "x",
            "GUILD_ID": "1",
            "DATA_DIR": str(tmp_path),
            "CS2_CHANNEL_ID": "123",
            "CS2_POLL_SECONDS": "90",
        }
    )
    bot.add_cog = AsyncMock()
    await cs2_cog.setup(bot)
    cog = bot.add_cog.call_args.args[0]
    assert cog.auto_post.channel_id == 123
    assert cog.auto_post.poll_seconds == 90


async def test_cog_unload_closes_client() -> None:
    client = LeetifyClient()
    client.aclose = AsyncMock()  # type: ignore[method-assign]
    cog = Cs2(client, MemoryLinkStore())
    await cog.cog_unload()
    client.aclose.assert_awaited_once()


async def test_cog_unload_closes_any_provider_once() -> None:
    stats = FakeStats()
    cog = Cs2(stats, MemoryLinkStore())
    await cog.cog_unload()
    assert stats.close_calls == 1


async def test_cog_unload_closes_stats_and_resolver() -> None:
    cog, stats, _, resolver, _ = vanity_cog()
    await cog.cog_unload()
    assert stats.close_calls == 1
    assert resolver.close_calls == 1


async def test_cog_unload_closes_resolver_when_stats_close_fails() -> None:
    cog, stats, _, resolver, _ = vanity_cog()
    stats.aclose = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await cog.cog_unload()
    assert resolver.close_calls == 1


FIXTURES = Path(__file__).parent / "fixtures" / "leetify"
# The fixture profile/match player "alpha"; STEAM_ID ("bravo") is also in the match.
FIXTURE_STEAM_ID = "76561198000000000"
OTHER_USER_ID = 7


@pytest.fixture
def match() -> Match:
    return parse_match(json.loads((FIXTURES / "match.json").read_text()))


@pytest.fixture
def fixture_profile() -> Profile:
    return parse_profile(json.loads((FIXTURES / "profile.json").read_text()))


def make_member(user_id: int = OTHER_USER_ID, name: str = "Friend") -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    return member


def posted_embed(interaction: MagicMock) -> discord.Embed:
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert args == ()
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    return embed


def field_names(embed: discord.Embed) -> list[str]:
    return [field.name or "" for field in embed.fields]


def scoreboard_cog(
    match: Match,
    profile: Profile,
    links: dict[int, str] | None = None,
    **stats_kwargs: Any,
) -> tuple[Cs2, FakeStats, UserCooldown]:
    stats = FakeStats(profile, matches=[match], **stats_kwargs)
    cooldown = UserCooldown(COOLDOWN_SECONDS, FakeClock())
    store = MemoryLinkStore(links if links is not None else {USER_ID: FIXTURE_STEAM_ID})
    cog, _, _ = make_cog(stats, store, cooldown)
    return cog, stats, cooldown


async def test_scoreboard_own_latest_match(match: Match, fixture_profile: Profile) -> None:
    cog, stats, cooldown = scoreboard_cog(match, fixture_profile)
    interaction = make_interaction()
    await Cs2.scoreboard.callback(cog, interaction, None, None)
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    assert stats.recent_calls == [FIXTURE_STEAM_ID]
    assert stats.match_calls == []
    assert stats.profile_calls == [FIXTURE_STEAM_ID]
    embed = posted_embed(interaction)
    assert BREAKDOWN_FIELD in field_names(embed)
    assert cooldown.try_acquire(USER_ID) > 0  # cooldown kept after posting
    allowed = interaction.followup.send.call_args.kwargs["allowed_mentions"]
    assert allowed.to_dict() == discord.AllowedMentions.none().to_dict()


async def test_scoreboard_one_player_history_entry_posts_full_match(
    match: Match, fixture_profile: Profile
) -> None:
    focus = next(p for p in match.players if p.steam64_id == FIXTURE_STEAM_ID)
    history_entry = dataclasses.replace(match, players=(focus,))
    cog, stats, _ = scoreboard_cog(history_entry, fixture_profile, by_id=[match])
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    assert stats.match_calls == [match.id]
    assert len(match.players) == 10
    expected = build_match_embed(match, compute_awards(match), FIXTURE_STEAM_ID, fixture_profile)
    assert posted_embed(interaction).to_dict() == expected.to_dict()


async def test_scoreboard_explicit_match_id_does_not_refetch(
    match: Match, fixture_profile: Profile
) -> None:
    history_entry = dataclasses.replace(match, players=match.players[:1])
    cog, stats, _ = scoreboard_cog(history_entry, fixture_profile)
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, match=match.id)
    assert stats.recent_calls == []
    assert stats.match_calls == [match.id]


async def test_scoreboard_uses_first_recent_match(match: Match, fixture_profile: Profile) -> None:
    older = Match(id="old", map_name="de_old", finished_at=None, team_scores=(), players=())
    cog, stats, _ = scoreboard_cog(match, fixture_profile)
    stats.matches = [match, older]
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    assert match.map_name is not None
    assert match.map_name in (posted_embed(interaction).title or "")


async def test_scoreboard_other_member(match: Match, fixture_profile: Profile) -> None:
    cog, stats, cooldown = scoreboard_cog(
        match, fixture_profile, links={OTHER_USER_ID: FIXTURE_STEAM_ID}
    )
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, make_member())
    assert stats.recent_calls == [FIXTURE_STEAM_ID]
    assert "alpha" in field_names(posted_embed(interaction))
    # The cooldown belongs to the caller, not the target.
    assert cooldown.try_acquire(USER_ID) > 0
    assert cooldown.try_acquire(OTHER_USER_ID) == 0


async def test_scoreboard_explicit_match_id(match: Match, fixture_profile: Profile) -> None:
    cog, stats, _ = scoreboard_cog(match, fixture_profile)
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, None, f"  {match.id} ")
    assert stats.match_calls == [match.id]
    assert stats.recent_calls == []
    posted_embed(interaction)


async def test_scoreboard_focus_is_target_player(match: Match, fixture_profile: Profile) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile, links={USER_ID: STEAM_ID})
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    assert "bravo" in field_names(posted_embed(interaction))


async def test_scoreboard_not_linked_self(match: Match, fixture_profile: Profile) -> None:
    cog, stats, cooldown = scoreboard_cog(match, fixture_profile, links={})
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    assert reply(interaction) == ((NotLinkedError.default_message,), {"ephemeral": True})
    assert "/link" in NotLinkedError.default_message
    interaction.response.defer.assert_not_awaited()
    assert stats.recent_calls == stats.profile_calls == []
    assert cooldown.try_acquire(USER_ID) == 0  # never taken


async def test_scoreboard_not_linked_other(match: Match, fixture_profile: Profile) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile)
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, make_member(name="Some*one"))
    args, kwargs = reply(interaction)
    assert args == (not_linked_message("Some*one"),)
    assert kwargs == {"ephemeral": True}
    assert "Some\\*one" in args[0]
    interaction.response.defer.assert_not_awaited()


async def test_scoreboard_self_as_member_uses_self_message(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile, links={})
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, make_member(USER_ID))
    assert reply(interaction) == ((NotLinkedError.default_message,), {"ephemeral": True})


async def test_scoreboard_too_long_match_id_is_ephemeral(
    match: Match, fixture_profile: Profile
) -> None:
    cog, stats, _ = scoreboard_cog(match, fixture_profile)
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, None, "x" * (MAX_MATCH_ID_LENGTH + 1))
    assert reply(interaction) == ((INVALID_MATCH_ID_MESSAGE,), {"ephemeral": True})
    assert stats.match_calls == []


async def test_scoreboard_link_lookup_error_is_generic(
    match: Match, fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile)
    cog.links.get = AsyncMock(side_effect=RuntimeError("disk"))  # type: ignore[method-assign]
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error looking up a CS2 link" in caplog.text


async def test_scoreboard_no_matches_refunds(match: Match, fixture_profile: Profile) -> None:
    cog, stats, cooldown = scoreboard_cog(match, fixture_profile)
    stats.matches = []
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    interaction.followup.send.assert_awaited_once_with(NoMatchesError.default_message)
    assert cooldown.try_acquire(USER_ID) == 0  # refunded


async def test_scoreboard_profile_failure_still_posts(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, cooldown = scoreboard_cog(match, fixture_profile, error=PrivateProfileError())
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    embed = posted_embed(interaction)
    assert BREAKDOWN_FIELD not in field_names(embed)
    assert "alpha" in field_names(embed)
    assert cooldown.try_acquire(USER_ID) > 0


async def test_scoreboard_rate_limited(match: Match, fixture_profile: Profile) -> None:
    cog, _, cooldown = scoreboard_cog(match, fixture_profile, match_error=RateLimitedError())
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    interaction.followup.send.assert_awaited_once_with(RateLimitedError.default_message)
    assert "try again later" in RateLimitedError.default_message
    assert cooldown.try_acquire(USER_ID) == 0


async def test_scoreboard_unknown_match_id() -> None:
    fetch_urls: list[str] = []

    async def fetch(url: str, headers: Mapping[str, str]) -> FetchResponse:
        fetch_urls.append(url)
        return FetchResponse(404, "")

    store = MemoryLinkStore({USER_ID: FIXTURE_STEAM_ID})
    cog = Cs2(LeetifyClient(fetch=fetch), store, UserCooldown(COOLDOWN_SECONDS, FakeClock()))
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction, None, "nonsense")
    interaction.followup.send.assert_awaited_once_with(MatchNotFoundError.default_message)
    assert any("/v2/matches/nonsense" in url for url in fetch_urls)


async def test_scoreboard_unexpected_error_is_generic(
    match: Match, fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, cooldown = scoreboard_cog(match, fixture_profile, match_error=RuntimeError("x"))
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    interaction.followup.send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE)
    assert "Unexpected error building a CS2 scoreboard" in caplog.text
    assert cooldown.try_acquire(USER_ID) == 0


async def test_scoreboard_unexpected_profile_error_is_generic(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile, error=RuntimeError("x"))
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    interaction.followup.send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE)


class GatedStats(FakeStats):
    """FakeStats whose match and profile calls block until their gate is opened."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.match_started = asyncio.Event()
        self.profile_started = asyncio.Event()
        self.match_gate = asyncio.Event()
        self.profile_gate = asyncio.Event()
        self.profile_cancelled = False

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        self.match_started.set()
        await self.match_gate.wait()
        return await super().get_recent_matches(steam64_id)

    async def get_match(self, game_id: str) -> Match:
        self.match_started.set()
        await self.match_gate.wait()
        return await super().get_match(game_id)

    async def get_profile(self, steam64_id: str) -> Profile:
        self.profile_started.set()
        try:
            await self.profile_gate.wait()
        except asyncio.CancelledError:
            self.profile_cancelled = True
            raise
        return await super().get_profile(steam64_id)


def gated_cog(match: Match, profile: Profile, **stats_kwargs: Any) -> tuple[Cs2, GatedStats]:
    stats = GatedStats(profile, matches=[match], **stats_kwargs)
    store = MemoryLinkStore({USER_ID: FIXTURE_STEAM_ID})
    cog, _, _ = make_cog(stats, store, UserCooldown(COOLDOWN_SECONDS, FakeClock()))
    return cog, stats


async def wait_started(stats: GatedStats) -> None:
    async with asyncio.timeout(1):
        await stats.match_started.wait()
        await stats.profile_started.wait()


@pytest.mark.parametrize("match_id", [None, "explicit"])
async def test_scoreboard_fetches_match_and_profile_concurrently(
    match: Match, fixture_profile: Profile, match_id: str | None
) -> None:
    cog, stats = gated_cog(match, fixture_profile)
    interaction = make_interaction()
    game_id = match.id if match_id else None
    task = asyncio.create_task(cog.handle_scoreboard(interaction, None, game_id))
    # Both requests are in flight before either is allowed to finish.
    await wait_started(stats)
    interaction.followup.send.assert_not_awaited()
    stats.profile_gate.set()
    stats.match_gate.set()
    await task

    # Same embed as the sequential version.
    expected = build_match_embed(match, compute_awards(match), FIXTURE_STEAM_ID, fixture_profile)
    assert posted_embed(interaction).to_dict() == expected.to_dict()


async def test_scoreboard_match_error_cancels_slow_profile(
    match: Match, fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, stats = gated_cog(match, fixture_profile, match_error=RateLimitedError())
    interaction = make_interaction()
    task = asyncio.create_task(cog.handle_scoreboard(interaction))
    await wait_started(stats)
    stats.match_gate.set()  # the profile gate never opens
    async with asyncio.timeout(1):
        await task
    interaction.followup.send.assert_awaited_once_with(RateLimitedError.default_message)
    assert stats.profile_cancelled
    assert stats.profile_calls == []
    assert caplog.text == ""


async def test_scoreboard_profile_cs2_error_with_concurrent_match_posts(
    match: Match, fixture_profile: Profile
) -> None:
    cog, stats = gated_cog(match, fixture_profile, error=PrivateProfileError())
    interaction = make_interaction()
    task = asyncio.create_task(cog.handle_scoreboard(interaction))
    await wait_started(stats)
    stats.profile_gate.set()  # profile fails first, match is still in flight
    await asyncio.sleep(0)
    stats.match_gate.set()
    await task
    embed = posted_embed(interaction)
    assert BREAKDOWN_FIELD not in field_names(embed)
    assert "alpha" in field_names(embed)


async def test_scoreboard_unexpected_profile_error_with_concurrent_match(
    match: Match, fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, stats = gated_cog(match, fixture_profile, error=RuntimeError("boom"))
    interaction = make_interaction()
    task = asyncio.create_task(cog.handle_scoreboard(interaction))
    await wait_started(stats)
    stats.profile_gate.set()
    await asyncio.sleep(0)
    stats.match_gate.set()
    await task
    interaction.followup.send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE)
    assert "Unexpected error building a CS2 scoreboard" in caplog.text


async def test_scoreboard_match_and_profile_both_fail_leaves_no_tasks(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, _ = scoreboard_cog(
        match, fixture_profile, error=RuntimeError("profile"), match_error=ApiError()
    )
    interaction = make_interaction()
    before = asyncio.all_tasks()
    await cog.handle_scoreboard(interaction)
    interaction.followup.send.assert_awaited_once_with(ApiError.default_message)
    assert asyncio.all_tasks() == before


async def test_scoreboard_cooldown_blocks_second_use(
    match: Match, fixture_profile: Profile
) -> None:
    cog, stats, _ = scoreboard_cog(match, fixture_profile)
    await cog.handle_scoreboard(make_interaction())
    interaction = make_interaction()
    await cog.handle_scoreboard(interaction)
    assert reply(interaction) == ((cooldown_message(COOLDOWN_SECONDS),), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()
    assert len(stats.recent_calls) == 1


async def test_scoreboard_send_failure_is_swallowed_and_refunds(
    match: Match, fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, cooldown = scoreboard_cog(match, fixture_profile)
    interaction = make_interaction()
    interaction.followup.send = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=404, reason="x"), "gone")
    )
    await cog.handle_scoreboard(interaction)
    assert "Could not send CS2 response" in caplog.text
    assert cooldown.try_acquire(USER_ID) == 0


async def test_scoreboard_defer_failure_refunds(match: Match, fixture_profile: Profile) -> None:
    cog, _, cooldown = scoreboard_cog(match, fixture_profile)
    interaction = make_interaction()
    interaction.response.defer = AsyncMock(side_effect=RuntimeError("defer"))
    with pytest.raises(RuntimeError):
        await cog.handle_scoreboard(interaction)
    assert cooldown.try_acquire(USER_ID) == 0


def test_cooldown_message_rounds_up() -> None:
    assert cooldown_message(0.2) == "Slow down! You can use CS2 stats commands again in 1 second."
    assert "10 seconds" in cooldown_message(9.1)


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, None), ("", None), ("  ", None), (" abc ", "abc")]
)
def test_clean_match_id(raw: str | None, expected: str | None) -> None:
    assert clean_match_id(raw) == expected


def test_clean_match_id_too_long() -> None:
    with pytest.raises(Cs2Error, match="match id"):
        clean_match_id("x" * (MAX_MATCH_ID_LENGTH + 1))


def test_scoreboard_option_metadata() -> None:
    params = Cs2.scoreboard._params
    assert set(params) == {"player", "match"}
    assert not params["player"].required
    assert not params["match"].required
    assert str(params["player"].description)
    assert params["match"].to_dict()["max_length"] == MAX_MATCH_ID_LENGTH == 64


# /profile


def profile_cog(
    profile: Profile, links: dict[int, str] | None = None, **stats_kwargs: Any
) -> tuple[Cs2, FakeStats, UserCooldown]:
    stats = FakeStats(profile, **stats_kwargs)
    cooldown = UserCooldown(COOLDOWN_SECONDS, FakeClock())
    store = MemoryLinkStore(links if links is not None else {USER_ID: FIXTURE_STEAM_ID})
    cog, _, _ = make_cog(stats, store, cooldown)
    return cog, stats, cooldown


async def test_profile_own(fixture_profile: Profile) -> None:
    cog, stats, cooldown = profile_cog(fixture_profile)
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    embed = posted_embed(interaction)
    assert embed.title == "alpha"
    assert RATINGS_FIELD in field_names(embed)
    assert stats.profile_calls == [FIXTURE_STEAM_ID]
    assert cooldown.try_acquire(USER_ID) > 0  # kept


async def test_profile_command_callback_delegates(fixture_profile: Profile) -> None:
    cog, stats, _ = profile_cog(fixture_profile)
    interaction = make_interaction()
    await Cs2.profile.callback(cog, interaction)
    posted_embed(interaction)
    assert stats.profile_calls == [FIXTURE_STEAM_ID]


async def test_profile_other_member(fixture_profile: Profile) -> None:
    cog, stats, cooldown = profile_cog(fixture_profile, {OTHER_USER_ID: STEAM_ID})
    interaction = make_interaction()
    await cog.handle_profile(interaction, make_member())
    posted_embed(interaction)
    assert stats.profile_calls == [STEAM_ID]
    assert cooldown.try_acquire(USER_ID) > 0  # keyed on the caller


async def test_profile_not_linked_self(fixture_profile: Profile) -> None:
    cog, stats, cooldown = profile_cog(fixture_profile, {})
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    assert reply(interaction) == ((NotLinkedError.default_message,), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()
    assert stats.profile_calls == []
    assert cooldown.try_acquire(USER_ID) == 0


async def test_profile_not_linked_other(fixture_profile: Profile) -> None:
    cog, stats, _ = profile_cog(fixture_profile, {})
    interaction = make_interaction()
    await cog.handle_profile(interaction, make_member(name="Friend"))
    assert reply(interaction) == ((not_linked_message("Friend"),), {"ephemeral": True})
    assert stats.profile_calls == []


async def test_profile_link_lookup_error_is_generic(
    fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, _ = profile_cog(fixture_profile)
    cog.links.get = AsyncMock(side_effect=RuntimeError("disk"))  # type: ignore[method-assign]
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error looking up a CS2 link" in caplog.text


@pytest.mark.parametrize(
    "error", [PlayerNotFoundError(), PrivateProfileError(), RateLimitedError(), ApiError()]
)
async def test_profile_api_errors_refund(fixture_profile: Profile, error: Cs2Error) -> None:
    cog, _, cooldown = profile_cog(fixture_profile, error=error)
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    interaction.followup.send.assert_awaited_once_with(error.message)
    assert cooldown.try_acquire(USER_ID) == 0


async def test_profile_unexpected_error_is_generic(
    fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, cooldown = profile_cog(fixture_profile, error=RuntimeError("x"))
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    interaction.followup.send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE)
    assert "Unexpected error building a CS2 profile" in caplog.text
    assert cooldown.try_acquire(USER_ID) == 0


async def test_profile_cooldown_blocks_second_use(fixture_profile: Profile) -> None:
    cog, stats, _ = profile_cog(fixture_profile)
    await cog.handle_profile(make_interaction())
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    assert reply(interaction) == ((cooldown_message(COOLDOWN_SECONDS),), {"ephemeral": True})
    interaction.response.defer.assert_not_awaited()
    assert len(stats.profile_calls) == 1


async def test_profile_shares_cooldown_with_scoreboard(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile)
    await cog.handle_scoreboard(make_interaction())
    interaction = make_interaction()
    await cog.handle_profile(interaction)
    assert reply(interaction) == ((cooldown_message(COOLDOWN_SECONDS),), {"ephemeral": True})


async def test_profile_send_failure_is_swallowed_and_refunds(
    fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, cooldown = profile_cog(fixture_profile)
    interaction = make_interaction()
    interaction.followup.send = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=404, reason="x"), "gone")
    )
    await cog.handle_profile(interaction)
    assert "Could not send CS2 response" in caplog.text
    assert cooldown.try_acquire(USER_ID) == 0


async def test_profile_defer_failure_refunds(fixture_profile: Profile) -> None:
    cog, _, cooldown = profile_cog(fixture_profile)
    interaction = make_interaction()
    interaction.response.defer = AsyncMock(side_effect=RuntimeError("defer"))
    with pytest.raises(RuntimeError):
        await cog.handle_profile(interaction)
    assert cooldown.try_acquire(USER_ID) == 0


def test_profile_option_metadata() -> None:
    params = Cs2.profile._params
    assert set(params) == {"player"}
    assert not params["player"].required
    assert str(params["player"].description)


# --- /scoreboard with Discord identities ---

GOLF_STEAM_ID = "76561198000000006"  # Team B in the fixture match
FRIEND_ID = 7
ABSENT_ID = 8  # linked, but not a member of the server


class FakeGuild:
    """Members by ID; `fetch_member` raises NotFound for anyone else (like Discord)."""

    def __init__(self, members: Mapping[int, str]) -> None:
        self.members = dict(members)
        self.fetch_calls: list[int] = []

    def get_member(self, user_id: int) -> discord.Member | None:
        return None  # nothing cached, so every lookup goes through REST

    async def fetch_member(self, user_id: int) -> MagicMock:
        self.fetch_calls.append(user_id)
        if user_id not in self.members:
            raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
        return make_member(user_id, self.members[user_id])


class FailingLinkStore(MemoryLinkStore):
    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        raise RuntimeError("database is locked")


def guild_interaction(members: Mapping[int, str]) -> MagicMock:
    interaction = make_interaction()
    interaction.guild = FakeGuild(members)
    return interaction


def field_value(embed: discord.Embed, name: str) -> str:
    return next(field.value or "" for field in embed.fields if field.name == name)


FRIEND_LINKS = {USER_ID: FIXTURE_STEAM_ID, FRIEND_ID: GOLF_STEAM_ID, ABSENT_ID: STEAM_ID}
MEMBERS = {USER_ID: "Sena", FRIEND_ID: "Gizmo"}


async def test_scoreboard_shows_friends_by_discord_name(
    match: Match, fixture_profile: Profile
) -> None:
    cog, stats, _ = scoreboard_cog(match, fixture_profile, links=FRIEND_LINKS)
    interaction = guild_interaction(MEMBERS)
    await cog.handle_scoreboard(interaction)

    embed = posted_embed(interaction)
    assert len(match.players) == 10
    own_table = field_value(embed, YOUR_TEAM)
    assert "Sena" in own_table
    assert "alpha" not in own_table
    assert "bravo" in own_table  # linked, but not in the server
    other_table = field_value(embed, TEAM_NAMES[1])
    assert "Gizmo" in other_table
    assert "golf" not in other_table
    friends = field_value(embed, FRIENDS_FIELD)
    assert f"<@{FRIEND_ID}> as golf ({TEAM_NAMES[1]})" in friends
    assert f"<@{ABSENT_ID}>" not in friends
    assert field_value(embed, "alpha").startswith(f"<@{USER_ID}> · alpha\n")
    allowed = interaction.followup.send.call_args.kwargs["allowed_mentions"]
    assert allowed.to_dict() == discord.AllowedMentions.none().to_dict()
    # Identities come from the store and Discord only: no extra Leetify requests.
    assert stats.recent_calls == [FIXTURE_STEAM_ID]
    assert stats.match_calls == []
    assert stats.profile_calls == [FIXTURE_STEAM_ID]

    identities = {
        FIXTURE_STEAM_ID: Identity(FIXTURE_STEAM_ID, USER_ID, "Sena"),
        GOLF_STEAM_ID: Identity(GOLF_STEAM_ID, FRIEND_ID, "Gizmo"),
    }
    expected = build_match_embed(
        match, compute_awards(match), FIXTURE_STEAM_ID, fixture_profile, identities=identities
    )
    assert embed.to_dict() == expected.to_dict()


async def test_scoreboard_requester_only_gets_details(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile)
    interaction = guild_interaction({USER_ID: "Sena"})
    await cog.handle_scoreboard(interaction)

    embed = posted_embed(interaction)
    assert field_value(embed, "alpha").startswith(f"<@{USER_ID}> · alpha\n")
    assert "Sena" in field_value(embed, YOUR_TEAM)
    identities = {FIXTURE_STEAM_ID: Identity(FIXTURE_STEAM_ID, USER_ID, "Sena")}
    expected = build_match_embed(
        match, compute_awards(match), FIXTURE_STEAM_ID, fixture_profile, identities=identities
    )
    assert embed.to_dict() == expected.to_dict()


async def test_scoreboard_identity_failure_posts_without_identities(
    match: Match, fixture_profile: Profile, caplog: pytest.LogCaptureFixture
) -> None:
    stats = FakeStats(fixture_profile, matches=[match])
    cooldown = UserCooldown(COOLDOWN_SECONDS, FakeClock())
    cog, _, _ = make_cog(stats, FailingLinkStore(FRIEND_LINKS), cooldown)
    interaction = guild_interaction(MEMBERS)
    await cog.handle_scoreboard(interaction)

    expected = build_match_embed(match, compute_awards(match), FIXTURE_STEAM_ID, fixture_profile)
    assert posted_embed(interaction).to_dict() == expected.to_dict()
    assert "Posting scoreboard without Discord names" in caplog.text
    assert cooldown.try_acquire(USER_ID) > 0  # posted, so the cooldown is kept


async def test_scoreboard_without_guild_has_no_identities(
    match: Match, fixture_profile: Profile
) -> None:
    cog, _, _ = scoreboard_cog(match, fixture_profile, links=FRIEND_LINKS)
    interaction = make_interaction()  # guild is None
    await cog.handle_scoreboard(interaction)

    embed = posted_embed(interaction)
    assert FRIENDS_FIELD not in field_names(embed)
    expected = build_match_embed(match, compute_awards(match), FIXTURE_STEAM_ID, fixture_profile)
    assert embed.to_dict() == expected.to_dict()


async def test_scoreboard_one_player_history_entry_shows_friend_names(
    match: Match, fixture_profile: Profile
) -> None:
    focus = next(p for p in match.players if p.steam64_id == FIXTURE_STEAM_ID)
    history_entry = dataclasses.replace(match, players=(focus,))
    cog, stats, _ = scoreboard_cog(
        history_entry, fixture_profile, links=FRIEND_LINKS, by_id=[match]
    )
    interaction = guild_interaction(MEMBERS)
    await cog.handle_scoreboard(interaction)

    assert stats.match_calls == [match.id]
    embed = posted_embed(interaction)
    tables = field_value(embed, YOUR_TEAM) + field_value(embed, TEAM_NAMES[1])
    shown = {"alpha": "Sena", "golf": "Gizmo"}
    for player in match.players:
        assert shown.get(player.name or "", player.name or "") in tables
    assert "Gizmo" in field_value(embed, TEAM_NAMES[1])
    assert f"<@{FRIEND_ID}> as golf" in field_value(embed, FRIENDS_FIELD)


# --- /leaderboard ---


def squad_profile(steam64_id: str, name: str, aim: float | None = None) -> Profile:
    return dataclasses.replace(make_profile(steam64_id, name), aim=aim, winrate=0.5)


def squad_steam_id(discord_id: int) -> str:
    return f"7656119800000{discord_id:04d}"


class SquadStats(FakeStats):
    """Profiles and errors per Steam ID."""

    def __init__(self, results: Mapping[str, Profile | Exception]) -> None:
        super().__init__()
        self.results = dict(results)

    async def get_profile(self, steam64_id: str) -> Profile:
        self.profile_calls.append(steam64_id)
        result = self.results[steam64_id]
        if isinstance(result, Exception):
            raise result
        return result


def leaderboard_cog(
    results: Mapping[int, Profile | Exception],
) -> tuple[Cs2, SquadStats, UserCooldown]:
    """`results` maps Discord ID -> profile or error; Steam IDs come from `squad_steam_id`."""
    stats = SquadStats({squad_steam_id(d): r for d, r in results.items()})
    store = MemoryLinkStore({d: squad_steam_id(d) for d in results})
    cooldown = UserCooldown(LEADERBOARD_COOLDOWN_SECONDS, FakeClock())
    cog = Cs2(stats, store, UserCooldown(COOLDOWN_SECONDS), leaderboard_cooldown=cooldown)
    return cog, stats, cooldown


def squad(*entries: tuple[int, str, float | None]) -> dict[int, Profile | Exception]:
    return {d: squad_profile(squad_steam_id(d), name, aim) for d, name, aim in entries}


def leaderboard_lines(embed: discord.Embed) -> list[str]:
    return field_value(embed, LEADERBOARD_FIELD).split("\n")


async def test_leaderboard_ranks_linked_players() -> None:
    cog, stats, cooldown = leaderboard_cog(squad((1, "Alice", 50), (2, "Bob", 80), (3, "Cy", 65)))
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, Metric.AIM)
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    assert stats.profile_calls == [squad_steam_id(1), squad_steam_id(2), squad_steam_id(3)]
    _, kwargs = followup_reply(interaction)
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    embed = kwargs["embed"]
    assert metric_label(Metric.AIM) in (embed.title or "")
    lines = leaderboard_lines(embed)
    assert [line.split()[1] for line in lines] == ["<@2>", "<@3>", "<@1>"]
    assert embed.footer.text is None
    assert cooldown.try_acquire(USER_ID) > 0


async def test_leaderboard_fetches_sequentially() -> None:
    active = 0
    peak = 0
    cog, stats, _ = leaderboard_cog(squad((1, "A", 1), (2, "B", 2), (3, "C", 3)))
    original = stats.get_profile

    async def get_profile(steam64_id: str) -> Profile:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return await original(steam64_id)

    stats.get_profile = get_profile  # type: ignore[method-assign]
    await cog.handle_leaderboard(make_interaction(), Metric.AIM)
    assert peak == 1
    assert len(stats.profile_calls) == 3


async def test_leaderboard_defers_before_loading_links() -> None:
    calls: list[str] = []
    cog, _, _ = leaderboard_cog(squad((1, "A", 1)))
    original = cog.links.all_links

    async def all_links() -> dict[int, str]:
        calls.append("all_links")
        return await original()

    cog.links.all_links = all_links  # type: ignore[method-assign]
    interaction = make_interaction()
    interaction.response.defer = AsyncMock(side_effect=lambda **_: calls.append("defer"))
    await cog.handle_leaderboard(interaction)
    assert calls == ["defer", "all_links"]


@pytest.mark.parametrize("metric", list(Metric))
async def test_leaderboard_command_passes_each_metric(metric: Metric) -> None:
    cog, _, _ = leaderboard_cog(squad((1, "A", 1)))
    cog.handle_leaderboard = AsyncMock()  # type: ignore[method-assign]
    interaction = make_interaction()
    choice = app_commands.Choice(name=metric_label(metric), value=metric.value)
    await Cs2.leaderboard.callback(cog, interaction, choice)
    cog.handle_leaderboard.assert_awaited_once_with(interaction, metric)


async def test_leaderboard_command_defaults_to_rating() -> None:
    cog, _, _ = leaderboard_cog(squad((1, "A", 1)))
    cog.handle_leaderboard = AsyncMock()  # type: ignore[method-assign]
    interaction = make_interaction()
    await Cs2.leaderboard.callback(cog, interaction, None)
    cog.handle_leaderboard.assert_awaited_once_with(interaction, Metric.RATING)


@pytest.mark.parametrize("metric", list(Metric))
async def test_leaderboard_posts_each_metric(metric: Metric) -> None:
    cog, _, _ = leaderboard_cog(squad((1, "Alice", 50)))
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, metric)
    embed = posted_embed(interaction)
    assert embed.title == f"CS2 leaderboard: {metric_label(metric)}"
    assert "<@1>" in leaderboard_lines(embed)[0]


@pytest.mark.parametrize(
    "error", [PrivateProfileError(), PlayerNotFoundError(), ApiError()], ids=type
)
async def test_leaderboard_skips_failed_profiles(error: Cs2Error) -> None:
    results = squad((1, "Alice", 50), (3, "Cy", 60))
    results[2] = error
    results[4] = ApiError()
    cog, stats, cooldown = leaderboard_cog(results)
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, Metric.AIM)
    assert len(stats.profile_calls) == 4
    embed = posted_embed(interaction)
    lines = leaderboard_lines(embed)
    assert len(lines) == 2
    assert "<@3>" in lines[0]
    assert embed.footer.text == "Skipped: 2 (private or unavailable)"
    assert cooldown.try_acquire(USER_ID) > 0


async def test_leaderboard_rate_limit_posts_collected_players() -> None:
    results = squad((1, "Alice", 50), (2, "Bob", 60), (4, "Dee", 70))
    results[3] = RateLimitedError()
    cog, stats, cooldown = leaderboard_cog(results)
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, Metric.AIM)
    assert stats.profile_calls == [squad_steam_id(1), squad_steam_id(2), squad_steam_id(3)]
    embed = posted_embed(interaction)
    assert len(leaderboard_lines(embed)) == 2
    assert embed.footer.text == "Stopped early, Leetify is rate limiting us: 2 not loaded"
    assert cooldown.try_acquire(USER_ID) > 0


async def test_leaderboard_rate_limit_and_skips_share_the_footer() -> None:
    results = squad((1, "Alice", 50))
    results[2] = PrivateProfileError()
    results[3] = RateLimitedError()
    cog, _, _ = leaderboard_cog(results)
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, Metric.AIM)
    assert posted_embed(interaction).footer.text == (
        "Skipped: 1 (private or unavailable)"
        " · Stopped early, Leetify is rate limiting us: 1 not loaded"
    )


async def test_leaderboard_rate_limited_first_replies_and_refunds() -> None:
    results = squad((2, "Bob", 60))
    results[1] = RateLimitedError()
    cog, stats, cooldown = leaderboard_cog(results)
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, Metric.AIM)
    assert stats.profile_calls == [squad_steam_id(1)]
    assert followup_reply(interaction) == ((RateLimitedError.default_message,), {})
    assert cooldown.try_acquire(USER_ID) == 0


async def test_leaderboard_no_links_posts_empty_state_and_refunds() -> None:
    cog, stats, cooldown = leaderboard_cog({})
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction)
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    assert stats.profile_calls == []
    assert posted_embed(interaction).description == NOBODY_LINKED_MESSAGE
    assert cooldown.try_acquire(USER_ID) == 0


async def test_leaderboard_all_failures_reply_error_and_refund() -> None:
    cog, _, cooldown = leaderboard_cog({1: PrivateProfileError(), 2: ApiError()})
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction)
    assert followup_reply(interaction) == ((ALL_PROFILES_FAILED_MESSAGE,), {})
    assert cooldown.try_acquire(USER_ID) == 0


async def test_leaderboard_fetches_at_most_the_link_cap() -> None:
    count = MAX_LEADERBOARD_LINKS + 5
    cog, stats, _ = leaderboard_cog(squad(*((d, f"P{d}", float(d)) for d in range(1, count + 1))))
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction, Metric.AIM)
    assert MAX_LEADERBOARD_LINKS == 25
    assert stats.profile_calls == [squad_steam_id(d) for d in range(1, MAX_LEADERBOARD_LINKS + 1)]
    embed = posted_embed(interaction)
    shown = "\n".join(f.value or "" for f in embed.fields).split("\n")
    assert len(shown) == MAX_LEADERBOARD_LINKS
    assert embed.footer.text == "Only the first 25 linked players are checked"


async def test_leaderboard_cooldown_blocks_second_use() -> None:
    cog, stats, _ = leaderboard_cog(squad((1, "A", 1)))
    await cog.handle_leaderboard(make_interaction())
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction)
    assert len(stats.profile_calls) == 1
    assert reply(interaction) == (
        (cooldown_message(LEADERBOARD_COOLDOWN_SECONDS),),
        {"ephemeral": True},
    )
    interaction.response.defer.assert_not_awaited()


async def test_leaderboard_cooldown_is_separate_from_stats_cooldown() -> None:
    cog, _, _ = leaderboard_cog(squad((1, "A", 1)))
    await cog.handle_leaderboard(make_interaction())
    assert cog.cooldown.try_acquire(USER_ID) == 0


async def test_leaderboard_unexpected_error_is_generic(caplog: pytest.LogCaptureFixture) -> None:
    cog, _, cooldown = leaderboard_cog({1: RuntimeError("boom")})
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction)
    assert followup_reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {})
    assert "Unexpected error building a CS2 leaderboard" in caplog.text
    assert cooldown.try_acquire(USER_ID) == 0


async def test_leaderboard_store_error_is_generic(caplog: pytest.LogCaptureFixture) -> None:
    cog, _, _ = leaderboard_cog({})
    cog.links.all_links = AsyncMock(side_effect=OSError("disk"))  # type: ignore[method-assign]
    interaction = make_interaction()
    await cog.handle_leaderboard(interaction)
    assert followup_reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {})
    assert "Unexpected error building a CS2 leaderboard" in caplog.text


async def test_leaderboard_send_failure_is_swallowed_and_refunds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cog, _, cooldown = leaderboard_cog(squad((1, "A", 1)))
    interaction = make_interaction()
    response = MagicMock(status=404, reason="Not Found")
    interaction.followup.send = AsyncMock(side_effect=discord.NotFound(response, "expired"))
    await cog.handle_leaderboard(interaction)
    assert "Could not send CS2 response" in caplog.text
    assert cooldown.try_acquire(USER_ID) == 0


async def test_leaderboard_error_send_failure_is_swallowed() -> None:
    cog, _, _ = leaderboard_cog({1: ApiError()})
    interaction = make_interaction()
    response = MagicMock(status=500, reason="Server Error")
    interaction.followup.send = AsyncMock(side_effect=discord.HTTPException(response, "x"))
    await cog.handle_leaderboard(interaction)
    interaction.followup.send.assert_awaited_once()


def test_leaderboard_option_metadata() -> None:
    assert Cs2.leaderboard.description == "Rank everyone who linked CS2 stats"
    params = Cs2.leaderboard._params
    assert set(params) == {"metric"}
    metric = params["metric"]
    assert not metric.required
    assert str(metric.description)
    assert [(c.name, c.value) for c in metric.choices] == [
        (metric_label(m), m.value) for m in Metric
    ]
