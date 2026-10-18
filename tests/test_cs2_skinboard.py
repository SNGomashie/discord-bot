from collections.abc import Mapping

from ninja_bot.cs2.embed import (
    LEADERBOARD_CONTINUED_FIELD,
    LEADERBOARD_FIELD,
    MAX_LEADERBOARD_ROWS,
    NO_PUBLIC_INVENTORIES_MESSAGE,
    NOBODY_LINKED_MESSAGE,
    SKINBOARD_TITLE,
    build_skinboard_embed,
    format_skinboard_row,
)
from ninja_bot.cs2.inventory import InventoryItem, ItemKind
from ninja_bot.cs2.models import ApiError, PrivateInventoryError, RateLimitedError
from ninja_bot.cs2.prices import CURRENCIES, PriceBook, Prices
from ninja_bot.cs2.squad import (
    SKINBOARD_LIMIT,
    SkinboardFetch,
    WorthEntry,
    WorthRow,
    fetch_skinboard,
    rank_worth,
    skinboard_notes,
)

EUR = CURRENCIES["EUR"]
SKIN = "AK-47 | Redline (Field-Tested)"
CASE = "Kilowatt Case"


def item(name: str, marketable: bool = True) -> InventoryItem:
    return InventoryItem(
        market_hash_name=name,
        name=name,
        item_type="Rifle",
        marketable=marketable,
        colour=None,
        kind=ItemKind.SKIN,
    )


BOOK = PriceBook({SKIN: Prices(steam=1000, skinport=900), CASE: Prices(steam=50, skinport=None)})


def steam_id(n: int) -> str:
    return f"7656119800000{n:04d}"


class FakeProvider:
    """Inventories by steam id; errors by steam id; each call advances the clock by `step`."""

    def __init__(
        self,
        inventories: Mapping[str, list[InventoryItem]],
        errors: Mapping[str, Exception] | None = None,
        step: float = 0.0,
    ) -> None:
        self.inventories = dict(inventories)
        self.errors = dict(errors or {})
        self.step = step
        self.now = 0.0
        self.calls: list[str] = []

    def clock(self) -> float:
        return self.now

    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]:
        self.calls.append(steam64_id)
        self.now += self.step
        if steam64_id in self.errors:
            raise self.errors[steam64_id]
        return list(self.inventories.get(steam64_id, []))

    async def aclose(self) -> None:
        pass


def entry(discord_id: int, name: str | None, steam: int, skinport: int = 0) -> WorthEntry:
    return WorthEntry(discord_id, name, steam, skinport)


def test_rank_worth_orders_by_steam_then_skinport() -> None:
    rows = rank_worth(
        [entry(1, "A", 100, 50), entry(2, "B", 300, 0), entry(3, "C", 100, 80), entry(4, "D", 0)]
    )
    assert [(r.rank, r.entry.discord_id) for r in rows] == [(1, 2), (2, 3), (3, 1), (4, 4)]


def test_rank_worth_ties_share_rank_ordered_by_name() -> None:
    rows = rank_worth([entry(1, "zed", 100, 5), entry(2, "Amy", 100, 5), entry(3, None, 10)])
    assert [(r.rank, r.entry.name) for r in rows] == [(1, "Amy"), (1, "zed"), (3, None)]


def test_rank_worth_empty() -> None:
    assert rank_worth([]) == []


async def test_fetch_values_each_link_in_discord_id_order() -> None:
    provider = FakeProvider(
        {steam_id(1): [item(SKIN), item(SKIN), item(CASE)], steam_id(2): [item(CASE, False)]}
    )
    fetch = await fetch_skinboard(
        provider, BOOK, {2: steam_id(2), 1: steam_id(1)}, clock=provider.clock
    )
    assert provider.calls == [steam_id(1), steam_id(2)]
    assert fetch == SkinboardFetch({1: (2050, 1800), 2: (0, 0)}, 0, 0, rate_limited=False)


async def test_fetch_skips_private_and_failed() -> None:
    provider = FakeProvider(
        {steam_id(3): [item(SKIN)]},
        {steam_id(1): PrivateInventoryError(), steam_id(2): ApiError()},
    )
    links = {n: steam_id(n) for n in (1, 2, 3)}
    fetch = await fetch_skinboard(provider, BOOK, links, clock=provider.clock)
    assert fetch.worth == {3: (1000, 900)}
    assert fetch.skipped == 2
    assert fetch.not_loaded == 0


async def test_fetch_unexpected_error_is_skipped() -> None:
    provider = FakeProvider({}, {steam_id(1): RuntimeError("boom")})
    fetch = await fetch_skinboard(provider, BOOK, {1: steam_id(1)}, clock=provider.clock)
    assert fetch.skipped == 1


async def test_fetch_rate_limit_stops() -> None:
    provider = FakeProvider({steam_id(1): [item(SKIN)]}, {steam_id(2): RateLimitedError()})
    links = {n: steam_id(n) for n in (1, 2, 3, 4)}
    fetch = await fetch_skinboard(provider, BOOK, links, clock=provider.clock)
    assert provider.calls == [steam_id(1), steam_id(2)]
    assert fetch == SkinboardFetch({1: (1000, 900)}, 0, 3, rate_limited=True)


async def test_fetch_stops_when_budget_is_used_up() -> None:
    provider = FakeProvider({}, step=10.0)
    links = {n: steam_id(n) for n in range(1, 6)}
    fetch = await fetch_skinboard(provider, BOOK, links, budget_seconds=25, clock=provider.clock)
    assert len(provider.calls) == 3
    assert fetch.not_loaded == 2
    assert not fetch.rate_limited
    assert set(fetch.worth) == {1, 2, 3}


async def test_fetch_is_capped() -> None:
    provider = FakeProvider({})
    links = {n: steam_id(n) for n in range(SKINBOARD_LIMIT + 5, 0, -1)}
    fetch = await fetch_skinboard(provider, BOOK, links, clock=provider.clock)
    assert provider.calls == [steam_id(n) for n in range(1, SKINBOARD_LIMIT + 1)]
    assert len(fetch.worth) == SKINBOARD_LIMIT


def test_notes() -> None:
    assert skinboard_notes(SkinboardFetch({}, 0, 0, False), 3) == []
    assert skinboard_notes(SkinboardFetch({}, 2, 3, False), SKINBOARD_LIMIT + 1) == [
        "2 skipped (private or unavailable)",
        "Stopped early, out of time: 3 not loaded",
        f"Only the first {SKINBOARD_LIMIT} linked members are checked",
    ]
    assert skinboard_notes(SkinboardFetch({}, 0, 1, True), 1) == [
        "Stopped early, Steam is rate limiting us: 1 not loaded"
    ]


def test_row_format_with_medal_and_escaped_name() -> None:
    row = WorthRow(1, entry(5, "*bold*", 123450, 110000))
    assert format_skinboard_row(row, EUR) == (
        "🥇 <@5> \\*bold\\* · Steam **€1,234.50** · Skinport €1,100.00"
    )


def test_row_format_without_name_and_past_medals() -> None:
    assert format_skinboard_row(WorthRow(4, entry(5, None, 0, 0)), EUR) == (
        "4. <@5> · Steam **€0.00** · Skinport €0.00"
    )


def test_embed_lists_rows() -> None:
    embed = build_skinboard_embed(rank_worth([entry(1, "A", 10), entry(2, "B", 20)]), EUR)
    assert embed.title == SKINBOARD_TITLE
    assert embed.description is None
    assert [f.name for f in embed.fields] == [LEADERBOARD_FIELD]
    assert (embed.fields[0].value or "").splitlines()[0].startswith("🥇 <@2> B")


def test_embed_empty_states() -> None:
    assert build_skinboard_embed([], EUR).description == NO_PUBLIC_INVENTORIES_MESSAGE
    embed = build_skinboard_embed([], EUR, NOBODY_LINKED_MESSAGE)
    assert embed.description == NOBODY_LINKED_MESSAGE
    assert embed.fields == []


def test_embed_packs_long_lists_and_caps_rows() -> None:
    rows = rank_worth([entry(n, "N" * 32, n) for n in range(1, MAX_LEADERBOARD_ROWS + 4)])
    embed = build_skinboard_embed(rows, EUR)
    assert embed.fields[0].name == LEADERBOARD_FIELD
    assert all(f.name == LEADERBOARD_CONTINUED_FIELD for f in embed.fields[1:])
    assert all(len(f.value or "") <= 1024 for f in embed.fields)
    assert sum(len((f.value or "").splitlines()) for f in embed.fields) == MAX_LEADERBOARD_ROWS
    assert embed.footer.text == "+3 more"
