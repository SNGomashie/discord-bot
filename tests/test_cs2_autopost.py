import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ninja_bot.cogs import cs2 as cs2_cog
from ninja_bot.cogs.cs2 import AutoPost, Cs2, SkinSources
from ninja_bot.cs2.awards import compute_awards
from ninja_bot.cs2.channels import (
    CHANNEL_MISSING,
    CHANNEL_NO_PERMISSIONS,
    CHANNEL_NOT_TEXT,
    CHANNEL_UNAVAILABLE,
)
from ninja_bot.cs2.embed import TOP_SKINS_FIELD, build_announcement_embed
from ninja_bot.cs2.identities import Identity
from ninja_bot.cs2.inventory import InventoryItem, ItemKind
from ninja_bot.cs2.models import Match, PlayerMatchStats, TeamScore
from ninja_bot.cs2.poller import NewMatch, PollResult
from ninja_bot.cs2.prices import CURRENCIES, DEFAULT_CURRENCY, PriceBook, Prices
from ninja_bot.cs2.squad import SquadMember

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
CHANNEL_ID = 555


def make_match(match_id: str, *steam64_ids: str) -> Match:
    return Match(
        id=match_id,
        map_name="de_mirage",
        finished_at=None,
        team_scores=(TeamScore(2, 13), TeamScore(3, 7)),
        players=tuple(
            PlayerMatchStats(steam64_id=s, name=f"P{s[-1]}", team_number=2 + i % 2, kills=10)
            for i, s in enumerate(steam64_ids)
        ),
    )


def new_match(match_id: str) -> NewMatch:
    return NewMatch(make_match(match_id, STEAM_A, STEAM_B), (SquadMember(1, STEAM_A),))


class FakePoller:
    def __init__(
        self, events: list[str], *results: list[NewMatch] | PollResult | Exception
    ) -> None:
        self.events = events
        self.results = list(results)
        self.poll_calls = 0
        self.confirmed: list[str] = []

    async def poll(self) -> PollResult:
        self.poll_calls += 1
        result = self.results.pop(0) if self.results else []
        if isinstance(result, Exception):
            raise result
        if isinstance(result, PollResult):
            return result
        return PollResult(result)

    async def confirm(self, new_match: NewMatch) -> None:
        self.events.append(f"confirm {new_match.match.id}")
        self.confirmed.append(new_match.match.id)


class MemoryLinks:
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

    async def discord_ids_for(self, steam64_ids: Any) -> dict[str, int]:
        wanted = set(steam64_ids)
        return {s: d for d, s in sorted(self.links.items(), reverse=True) if s in wanted}


class BrokenLinks(MemoryLinks):
    async def discord_ids_for(self, steam64_ids: Any) -> dict[str, int]:
        raise RuntimeError("db down")


ALL_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)


def make_member(name: str) -> MagicMock:
    member = MagicMock()
    member.display_name = name
    return member


def make_channel(
    events: list[str],
    permissions: discord.Permissions = ALL_PERMISSIONS,
    spec: type = discord.TextChannel,
) -> MagicMock:
    channel = MagicMock(spec=spec)
    channel.guild = MagicMock()
    channel.guild.get_member.side_effect = lambda discord_id: make_member(f"Member{discord_id}")
    channel.permissions_for.return_value = permissions

    async def send(**kwargs: Any) -> None:
        events.append(f"send {kwargs['embed'].title}")

    channel.send = AsyncMock(side_effect=send)
    return channel


def make_bot(channel: object | None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
    )
    bot.wait_until_ready = AsyncMock()
    return bot


def make_cog(
    poller: FakePoller,
    bot: MagicMock,
    channel_id: int | None = CHANNEL_ID,
    poll_seconds: float = 60,
    skins: SkinSources | None = None,
) -> Cs2:
    auto_post = AutoPost(bot, poller, channel_id, poll_seconds)  # type: ignore[arg-type]
    return Cs2(MagicMock(), MemoryLinks({1: STEAM_A}), auto_post=auto_post, skins=skins)


def sent_embeds(channel: MagicMock) -> list[discord.Embed]:
    return [call.kwargs["embed"] for call in channel.send.call_args_list]


async def test_posts_one_match_then_confirms() -> None:
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    poller = FakePoller(events, [match])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    identities = {STEAM_A: Identity(STEAM_A, 1, "Member1")}
    expected = build_announcement_embed(
        match.match, compute_awards(match.match), match.squad, identities=identities
    )
    [embed] = sent_embeds(channel)
    assert embed.to_dict() == expected.to_dict()
    assert poller.confirmed == ["m1"]
    assert events == [f"send {embed.title}", "confirm m1"]


async def test_announcement_gets_none_when_identity_lookup_fails() -> None:
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    cog = make_cog(FakePoller(events, [match]), make_bot(channel))
    cog.links = BrokenLinks()  # type: ignore[assignment]
    await cog.run_poll_cycle()
    expected = build_announcement_embed(
        match.match, compute_awards(match.match), match.squad, identities=None
    )
    [embed] = sent_embeds(channel)
    assert embed.to_dict() == expected.to_dict()
    assert "<@1>" in embed.to_dict()["fields"][-1]["value"]


async def test_announcement_without_members_has_no_mentions() -> None:
    events: list[str] = []
    channel = make_channel(events)
    channel.guild.get_member.side_effect = None
    channel.guild.get_member.return_value = None
    channel.guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "gone")
    )
    match = new_match("m1")
    cog = make_cog(FakePoller(events, [match]), make_bot(channel))
    await cog.run_poll_cycle()
    expected = build_announcement_embed(
        match.match, compute_awards(match.match), match.squad, identities={}
    )
    [embed] = sent_embeds(channel)
    assert embed.to_dict() == expected.to_dict()
    assert "<@" not in embed.to_dict()["fields"][-1]["value"]


async def test_posts_two_matches_in_order() -> None:
    events: list[str] = []
    channel = make_channel(events)
    first, second = new_match("m1"), new_match("m2")
    second = NewMatch(make_match("m2", STEAM_B, STEAM_A), second.squad)
    poller = FakePoller(events, [first, second])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    assert poller.confirmed == ["m1", "m2"]
    assert [e.split()[0] for e in events] == ["send", "confirm", "send", "confirm"]
    assert channel.send.await_count == 2


async def test_sends_never_ping() -> None:
    events: list[str] = []
    channel = make_channel(events)
    cog = make_cog(FakePoller(events, [new_match("m1")]), make_bot(channel))
    await cog.run_poll_cycle()
    mentions = channel.send.call_args.kwargs["allowed_mentions"]
    assert mentions.to_dict() == discord.AllowedMentions.none().to_dict()


async def test_failed_send_is_still_confirmed(caplog: pytest.LogCaptureFixture) -> None:
    events: list[str] = []
    channel = make_channel(events)
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "boom"))
    poller = FakePoller(events, [new_match("m1"), new_match("m2")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    assert poller.confirmed == ["m1", "m2"]
    assert "Could not send CS2 response" in caplog.text


async def test_embed_error_is_confirmed_and_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> discord.Embed:
        raise RuntimeError("bug")

    monkeypatch.setattr("ninja_bot.cogs.cs2.build_announcement_embed", broken)
    events: list[str] = []
    channel = make_channel(events)
    poller = FakePoller(events, [new_match("m1")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    assert poller.confirmed == ["m1"]
    channel.send.assert_not_awaited()
    assert "Unexpected error in a CS2 poll cycle" in caplog.text


async def test_channel_is_fetched_when_not_cached() -> None:
    events: list[str] = []
    channel = make_channel(events)
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(return_value=channel)
    cog = make_cog(FakePoller(events, [new_match("m1")]), bot)
    await cog.run_poll_cycle()
    bot.fetch_channel.assert_awaited_once_with(CHANNEL_ID)
    assert channel.send.await_count == 1


async def test_missing_channel_warns_once_and_skips_poll(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    poller = FakePoller(events, [new_match("m1")], [new_match("m2")])
    cog = make_cog(poller, make_bot(None))
    with caplog.at_level(logging.WARNING):
        await cog.run_poll_cycle()
        await cog.run_poll_cycle()
    assert poller.poll_calls == 0
    assert poller.confirmed == []
    assert caplog.text.count(CHANNEL_MISSING) == 1


async def test_forbidden_fetch_counts_as_missing(caplog: pytest.LogCaptureFixture) -> None:
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    poller = FakePoller([])
    cog = make_cog(poller, bot)
    await cog.run_poll_cycle()
    assert poller.poll_calls == 0
    assert CHANNEL_MISSING in caplog.text


async def test_fetch_http_error_skips_the_cycle(caplog: pytest.LogCaptureFixture) -> None:
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=503), "Service Unavailable")
    )
    poller = FakePoller([], [new_match("m1")])
    cog = make_cog(poller, bot)
    with caplog.at_level(logging.WARNING):
        await cog.run_poll_cycle()
        await cog.run_poll_cycle()
    assert poller.poll_calls == 0
    assert (
        caplog.text.count(
            f"Not posting CS2 matches: the CS2_CHANNEL_ID channel is {CHANNEL_UNAVAILABLE}"
        )
        == 1
    )
    assert "Unexpected error" not in caplog.text


async def test_non_text_channel_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    poller = FakePoller([])
    cog = make_cog(poller, make_bot(MagicMock(spec=discord.CategoryChannel)))
    await cog.run_poll_cycle()
    await cog.run_poll_cycle()
    assert poller.poll_calls == 0
    assert caplog.text.count(CHANNEL_NOT_TEXT) == 1


@pytest.mark.parametrize(
    "permissions",
    [
        discord.Permissions(send_messages=True, embed_links=True),
        discord.Permissions(view_channel=True, embed_links=True),
        discord.Permissions(view_channel=True, send_messages=True),
    ],
)
async def test_missing_permissions_warn_once_and_skip(
    permissions: discord.Permissions, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[str] = []
    channel = make_channel(events, permissions)
    poller = FakePoller(events, [new_match("m1")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    await cog.run_poll_cycle()
    channel.permissions_for.assert_called_with(channel.guild.me)
    assert poller.poll_calls == 0
    channel.send.assert_not_awaited()
    assert caplog.text.count(CHANNEL_NO_PERMISSIONS) == 1


async def test_thread_needs_send_in_threads() -> None:
    events: list[str] = []
    permissions = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
    channel = make_channel(events, permissions, spec=discord.Thread)
    poller = FakePoller(events, [new_match("m1")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    assert poller.poll_calls == 0
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages_in_threads=True, embed_links=True
    )
    await cog.run_poll_cycle()
    assert poller.confirmed == ["m1"]


async def test_warning_repeats_after_channel_recovers(caplog: pytest.LogCaptureFixture) -> None:
    events: list[str] = []
    channel = make_channel(events)
    bot = make_bot(None)
    cog = make_cog(FakePoller(events), bot)
    await cog.run_poll_cycle()
    bot.get_channel.return_value = channel
    await cog.run_poll_cycle()
    bot.get_channel.return_value = None
    await cog.run_poll_cycle()
    assert caplog.text.count(CHANNEL_MISSING) == 2


async def test_poll_exception_is_logged_and_loop_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    channel = make_channel(events)
    poller = FakePoller(events, RuntimeError("boom"), [new_match("m1")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    assert "Unexpected error in a CS2 poll cycle" in caplog.text
    await cog.run_poll_cycle()
    assert poller.confirmed == ["m1"]


async def test_rate_limit_announces_partial_then_skips_next_cycle_once() -> None:
    events: list[str] = []
    channel = make_channel(events)
    poller = FakePoller(events, PollResult([new_match("m1")], rate_limited=True), [new_match("m2")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    assert poller.confirmed == ["m1"]
    await cog.run_poll_cycle()
    assert poller.poll_calls == 1
    await cog.run_poll_cycle()
    assert poller.poll_calls == 2
    assert poller.confirmed == ["m1", "m2"]


async def test_no_backoff_without_rate_limit() -> None:
    events: list[str] = []
    channel = make_channel(events)
    poller = FakePoller(events, PollResult([new_match("m1")]), [new_match("m2")])
    cog = make_cog(poller, make_bot(channel))
    await cog.run_poll_cycle()
    await cog.run_poll_cycle()
    assert poller.poll_calls == 2
    assert poller.confirmed == ["m1", "m2"]


async def test_cycle_without_channel_does_nothing() -> None:
    poller = FakePoller([])
    bot = make_bot(None)
    cog = make_cog(poller, bot, channel_id=None)
    await cog.run_poll_cycle()
    assert poller.poll_calls == 0
    bot.get_channel.assert_not_called()


async def test_loop_starts_only_with_channel_and_stops_on_unload() -> None:
    bot = make_bot(None)
    bot.wait_until_ready = AsyncMock(side_effect=asyncio.Event().wait)
    cog = make_cog(FakePoller([]), bot, poll_seconds=60)
    cog.stats.aclose = AsyncMock()
    await cog.cog_load()
    assert cog.poll_loop.is_running()
    assert cog.poll_loop.seconds == 60
    await asyncio.sleep(0)
    bot.wait_until_ready.assert_awaited_once()
    await cog.cog_unload()
    await asyncio.sleep(0)
    assert not cog.poll_loop.is_running()


async def test_loop_not_started_without_channel(caplog: pytest.LogCaptureFixture) -> None:
    cog = make_cog(FakePoller([]), make_bot(None), channel_id=None)
    with caplog.at_level(logging.DEBUG, logger="ninja_bot.cogs.cs2"):
        await cog.cog_load()
    assert not cog.poll_loop.is_running()
    assert "auto-posts are off" in caplog.text


async def test_loop_not_started_without_auto_post() -> None:
    cog = Cs2(MagicMock(), MemoryLinks())
    await cog.cog_load()
    assert not cog.poll_loop.is_running()


SKIN_NAME = "AK-47 | Redline (Field-Tested)"
SKIN = InventoryItem(
    market_hash_name=SKIN_NAME,
    name=SKIN_NAME,
    item_type="Classified Rifle",
    marketable=True,
    colour=None,
    kind=ItemKind.SKIN,
)


class FakeInventories:
    def __init__(
        self,
        by_id: dict[str, list[InventoryItem]],
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.by_id = by_id
        self.error = error
        self.delay = delay
        self.calls: list[str] = []

    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]:
        self.calls.append(steam64_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return list(self.by_id.get(steam64_id, []))

    async def aclose(self) -> None:
        pass


def skin_sources(inventories: FakeInventories, book: PriceBook | None = None) -> SkinSources:
    if book is None:
        book = PriceBook({SKIN_NAME: Prices(steam=1230, skinport=1005)})
    return SkinSources(inventories, book, CURRENCIES[DEFAULT_CURRENCY])


def plain_announcement(match: NewMatch) -> dict[str, Any]:
    identities = {STEAM_A: Identity(STEAM_A, 1, "Member1")}
    embed = build_announcement_embed(
        match.match, compute_awards(match.match), match.squad, identities=identities
    )
    return embed.to_dict()


async def test_announcement_shows_top_skins_with_owners() -> None:
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    inventories = FakeInventories({STEAM_A: [SKIN], STEAM_B: [SKIN]})
    cog = make_cog(FakePoller(events, [match]), make_bot(channel), skins=skin_sources(inventories))
    await cog.run_poll_cycle()
    assert inventories.calls == [STEAM_A, STEAM_B]
    [embed] = sent_embeds(channel)
    field = embed.fields[-1]
    assert field.name == TOP_SKINS_FIELD
    value = field.value or ""
    assert "Redline" in value
    assert "Member1" in value and "P2" in value  # identity name, else Steam name
    assert "<@" not in value
    allowed = channel.send.call_args.kwargs["allowed_mentions"]
    assert allowed.to_dict() == discord.AllowedMentions.none().to_dict()
    assert events == [f"send {embed.title}", "confirm m1"]


async def test_announcement_without_skins_on_inventory_failure() -> None:
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    inventories = FakeInventories({}, error=RuntimeError("steam down"))
    poller = FakePoller(events, [match])
    cog = make_cog(poller, make_bot(channel), skins=skin_sources(inventories))
    await cog.run_poll_cycle()
    [embed] = sent_embeds(channel)
    assert embed.to_dict() == plain_announcement(match)
    assert poller.confirmed == ["m1"]


async def test_announcement_skins_error_is_still_posted_and_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("bad book")

    monkeypatch.setattr(cs2_cog, "match_top_skins", broken)
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    inventories = FakeInventories({STEAM_A: [SKIN]})
    cog = make_cog(FakePoller(events, [match]), make_bot(channel), skins=skin_sources(inventories))
    await cog.run_poll_cycle()
    [embed] = sent_embeds(channel)
    assert embed.to_dict() == plain_announcement(match)
    assert events == [f"send {embed.title}", "confirm m1"]


async def test_announcement_prices_loading_skips_inventories() -> None:
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    inventories = FakeInventories({STEAM_A: [SKIN]})
    skins = skin_sources(inventories, PriceBook())
    cog = make_cog(FakePoller(events, [match]), make_bot(channel), skins=skins)
    await cog.run_poll_cycle()
    assert inventories.calls == []
    [embed] = sent_embeds(channel)
    assert embed.to_dict() == plain_announcement(match)


async def test_announcement_skins_respect_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs2_cog, "MATCH_SKINS_BUDGET_SECONDS", 0.05)
    events: list[str] = []
    channel = make_channel(events)
    match = new_match("m1")
    inventories = FakeInventories({STEAM_A: [SKIN], STEAM_B: [SKIN]}, delay=0.1)
    cog = make_cog(FakePoller(events, [match]), make_bot(channel), skins=skin_sources(inventories))
    await cog.run_poll_cycle()
    assert inventories.calls == [STEAM_A]
    [embed] = sent_embeds(channel)
    value = embed.fields[-1].value or ""
    assert embed.fields[-1].name == TOP_SKINS_FIELD
    assert "Member1" in value and "P2" not in value
