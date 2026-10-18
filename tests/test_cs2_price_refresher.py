from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ninja_bot.cogs import cs2 as cs2_cog
from ninja_bot.cogs.cs2 import Cs2, PriceUpdates
from ninja_bot.config import load_config
from ninja_bot.cs2.models import ApiError, RateLimitedError
from ninja_bot.cs2.prices import (
    PRICE_CHECK_SECONDS,
    SKINPORT_REFRESH_SECONDS,
    STEAM_REFRESH_SECONDS,
    STEAM_RETRY_SECONDS,
    PriceBook,
    PriceRefresher,
    Prices,
    PriceSource,
)
from ninja_bot.cs2.skinport import SkinportClient
from ninja_bot.cs2.steam_market import CrawlResult, SteamMarketClient
from ninja_bot.cs2.store import SqliteLinkStore

START = 1_000_000.0


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class MemoryPriceStore:
    def __init__(self, prices: Mapping[str, Prices] | None = None) -> None:
        self.prices: dict[str, Prices] = dict(prices or {})
        self.updated: dict[PriceSource, float] = {}
        self.saves: list[tuple[PriceSource, dict[str, int], float]] = []
        self.fail_saves = False

    async def load_prices(self) -> dict[str, Prices]:
        return dict(self.prices)

    async def save_steam_prices(self, prices: Mapping[str, int], now: float) -> None:
        await self._save("steam", prices, now)

    async def save_skinport_prices(self, prices: Mapping[str, int], now: float) -> None:
        await self._save("skinport", prices, now)

    async def _save(self, source: PriceSource, prices: Mapping[str, int], now: float) -> None:
        if self.fail_saves:
            raise OSError("disk full")
        self.saves.append((source, dict(prices), now))
        if not prices:
            return
        for name, minor in prices.items():
            old = self.prices.get(name, Prices())
            self.prices[name] = (
                Prices(minor, old.skinport) if source == "steam" else Prices(old.steam, minor)
            )
        self.updated[source] = now

    async def last_update(self, source: PriceSource) -> float | None:
        return self.updated.get(source)


@dataclass
class FakeSteam:
    results: list[CrawlResult | Exception] = field(default_factory=list)
    calls: int = 0
    close_calls: int = 0

    async def crawl(self) -> CrawlResult:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        self.close_calls += 1


@dataclass
class FakeSkinport:
    results: list[dict[str, int] | Exception] = field(default_factory=list)
    calls: int = 0
    close_calls: int = 0
    close_error: Exception | None = None

    async def get_prices(self) -> dict[str, int]:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def complete(prices: dict[str, int], pages: int = 1) -> CrawlResult:
    return CrawlResult(prices, pages, complete=True)


def incomplete(prices: dict[str, int], pages: int = 1) -> CrawlResult:
    return CrawlResult(prices, pages, complete=False)


def make_refresher(
    store: MemoryPriceStore | None = None,
    steam: FakeSteam | None = None,
    skinport: FakeSkinport | None = None,
    clock: FakeClock | None = None,
) -> tuple[PriceRefresher, MemoryPriceStore, FakeSteam, FakeSkinport, FakeClock]:
    store = store if store is not None else MemoryPriceStore()
    steam = steam if steam is not None else FakeSteam()
    skinport = skinport if skinport is not None else FakeSkinport()
    clock = clock if clock is not None else FakeClock()
    refresher = PriceRefresher(PriceBook(), store, steam, skinport, clock)
    return refresher, store, steam, skinport, clock


def test_constants() -> None:
    assert PRICE_CHECK_SECONDS == 600
    assert SKINPORT_REFRESH_SECONDS == 3600
    assert STEAM_REFRESH_SECONDS == 86400
    assert STEAM_RETRY_SECONDS == 3600


async def test_everything_due_without_stored_prices() -> None:
    refresher, *_ = make_refresher()
    await refresher.load()
    assert refresher.due() == ["skinport", "steam"]
    assert refresher.book.is_empty


async def test_load_fills_book_and_update_times() -> None:
    store = MemoryPriceStore({"AK": Prices(100, 90), "Case": Prices(None, 5)})
    store.updated = {"steam": START - 10, "skinport": START - 10}
    refresher, *_ = make_refresher(store)
    await refresher.load()
    assert refresher.book.get("AK") == Prices(100, 90)
    assert refresher.book.get("Case") == Prices(None, 5)
    assert refresher.due() == []


async def test_due_after_each_interval() -> None:
    store = MemoryPriceStore()
    store.updated = {"steam": START, "skinport": START}
    refresher, _, _, _, clock = make_refresher(store)
    await refresher.load()
    clock.now = START + SKINPORT_REFRESH_SECONDS - 1
    assert refresher.due() == []
    clock.now = START + SKINPORT_REFRESH_SECONDS
    assert refresher.due() == ["skinport"]
    clock.now = START + STEAM_REFRESH_SECONDS
    assert refresher.due() == ["skinport", "steam"]


async def test_cycle_refreshes_both_and_saves() -> None:
    refresher, store, steam, skinport, clock = make_refresher(
        steam=FakeSteam([complete({"AK": 120})]),
        skinport=FakeSkinport([{"AK": 100, "Case": 5}]),
    )
    await refresher.load()
    await refresher.run_cycle()
    assert refresher.book.get("AK") == Prices(120, 100)
    assert refresher.book.get("Case") == Prices(None, 5)
    assert store.saves == [
        ("skinport", {"AK": 100, "Case": 5}, START),
        ("steam", {"AK": 120}, START),
    ]
    assert refresher.due() == []
    clock.now = START + SKINPORT_REFRESH_SECONDS
    await refresher.run_cycle()  # skinport results exhausted: an error, not a crash
    assert steam.calls == 1
    assert skinport.calls == 2


async def test_skinport_failure_keeps_prices_and_steam_still_runs() -> None:
    store = MemoryPriceStore({"AK": Prices(None, 100)})
    store.updated = {"skinport": START - SKINPORT_REFRESH_SECONDS}
    refresher, store, steam, _, _ = make_refresher(
        store,
        steam=FakeSteam([complete({"AK": 120})]),
        skinport=FakeSkinport([ApiError("down")]),
    )
    await refresher.load()
    await refresher.run_cycle()
    assert refresher.book.get("AK") == Prices(120, 100)
    assert steam.calls == 1
    assert [save[0] for save in store.saves] == ["steam"]
    # Skinport is still due; Steam is not.
    assert refresher.due() == ["skinport"]


async def test_steam_failure_keeps_prices_and_skinport_still_runs() -> None:
    store = MemoryPriceStore({"AK": Prices(120, None)})
    refresher, store, _, skinport, _ = make_refresher(
        store,
        steam=FakeSteam([RuntimeError("boom")]),
        skinport=FakeSkinport([{"AK": 100}]),
    )
    await refresher.load()
    await refresher.run_cycle()
    assert refresher.book.get("AK") == Prices(120, 100)
    assert skinport.calls == 1


async def test_skinport_error_is_raised_by_refresh() -> None:
    refresher, *_ = make_refresher(skinport=FakeSkinport([RateLimitedError("slow")]))
    with pytest.raises(RateLimitedError):
        await refresher.refresh_skinport()


async def test_empty_skinport_response_stays_due() -> None:
    refresher, *_ = make_refresher(skinport=FakeSkinport([{}]))
    await refresher.load()
    await refresher.refresh_skinport()
    assert "skinport" in refresher.due()


async def test_incomplete_crawl_saves_and_retries_sooner() -> None:
    refresher, store, steam, _, clock = make_refresher(
        steam=FakeSteam([incomplete({"AK": 120}), complete({"AK": 130, "M4": 50})]),
        skinport=FakeSkinport([{}, {}, {}]),
    )
    await refresher.load()
    await refresher.refresh_steam()
    assert refresher.book.get("AK") == Prices(120, None)
    assert store.saves == [("steam", {"AK": 120}, START)]
    clock.now = START + STEAM_RETRY_SECONDS - 1
    assert "steam" not in refresher.due()
    clock.now = START + STEAM_RETRY_SECONDS
    assert "steam" in refresher.due()
    await refresher.refresh_steam()
    assert refresher.book.get("AK") == Prices(130, None)
    assert refresher.book.get("M4") == Prices(50, None)
    clock.now += STEAM_REFRESH_SECONDS - 1
    assert "steam" not in refresher.due()
    clock.now += 1
    assert "steam" in refresher.due()
    assert steam.calls == 2


async def test_crawl_without_prices_retries_sooner() -> None:
    refresher, store, _, _, clock = make_refresher(steam=FakeSteam([complete({}, pages=0)]))
    await refresher.load()
    await refresher.refresh_steam()
    clock.now = START + STEAM_RETRY_SECONDS
    assert "steam" in refresher.due()


async def test_store_failure_never_raises_from_cycle() -> None:
    store = MemoryPriceStore()
    store.fail_saves = True
    refresher, *_ = make_refresher(
        store,
        steam=FakeSteam([complete({"AK": 120})]),
        skinport=FakeSkinport([{"AK": 100}]),
    )
    await refresher.load()
    await refresher.run_cycle()
    # The book still has the fresh prices; only persisting failed.
    assert refresher.book.get("AK") == Prices(120, 100)


async def test_aclose_closes_both_clients_even_on_error() -> None:
    skinport = FakeSkinport(close_error=RuntimeError("x"))
    refresher, _, steam, _, _ = make_refresher(skinport=skinport)
    with pytest.raises(RuntimeError):
        await refresher.aclose()
    assert steam.close_calls == 1
    assert skinport.close_calls == 1


class NullStats:
    async def aclose(self) -> None:
        pass


class NullResolver:
    async def aclose(self) -> None:
        pass


def price_cog(refresher: PriceRefresher, bot: MagicMock | None = None) -> tuple[Cs2, MagicMock]:
    bot = bot if bot is not None else MagicMock()
    cog = Cs2(
        NullStats(),  # type: ignore[arg-type]
        MagicMock(),
        resolver=NullResolver(),  # type: ignore[arg-type]
        price_updates=PriceUpdates(bot, refresher),
    )
    return cog, bot


async def test_setup_builds_price_refresher(tmp_path: Path) -> None:
    bot = MagicMock()
    bot.config = load_config(
        {"DISCORD_TOKEN": "x", "GUILD_ID": "1", "DATA_DIR": str(tmp_path), "SKIN_CURRENCY": "usd"}
    )
    bot.add_cog = AsyncMock()
    await cs2_cog.setup(bot)
    cog = bot.add_cog.call_args.args[0]
    assert cog.price_updates is not None
    assert cog.price_updates.bot is bot
    refresher = cog.price_updates.refresher
    assert isinstance(refresher._store, SqliteLinkStore)
    assert refresher._store is cog.links
    assert isinstance(refresher._steam, SteamMarketClient)
    assert refresher._steam._currency.code == "USD"
    assert isinstance(refresher._skinport, SkinportClient)
    assert refresher._skinport._currency.code == "USD"
    assert refresher.book.is_empty


async def test_cog_load_starts_price_loop_and_unload_closes_clients() -> None:
    store = MemoryPriceStore({"AK": Prices(100, 90)})
    store.updated = {"steam": START, "skinport": START}
    refresher, _, steam, skinport, _ = make_refresher(store)
    bot = MagicMock()
    ready = asyncio.Event()
    bot.wait_until_ready = AsyncMock(side_effect=ready.wait)
    cog, _ = price_cog(refresher, bot)
    await cog.cog_load()
    assert cog.price_loop.is_running()
    assert cog.price_loop.seconds == PRICE_CHECK_SECONDS
    ready.set()
    for _ in range(10):
        await asyncio.sleep(0)
    # Loaded from the store after the bot was ready; nothing due, so no requests.
    assert refresher.book.get("AK") == Prices(100, 90)
    assert steam.calls == skinport.calls == 0
    await cog.cog_unload()
    assert steam.close_calls == 1
    assert skinport.close_calls == 1


async def test_price_loop_load_failure_is_logged_not_raised() -> None:
    refresher, store, *_ = make_refresher()
    store.load_prices = AsyncMock(side_effect=OSError("locked"))  # type: ignore[method-assign]
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    cog, _ = price_cog(refresher, bot)
    await cog._before_price_loop()
    assert refresher.book.is_empty


async def test_price_loop_runs_a_cycle() -> None:
    refresher, _, steam, skinport, _ = make_refresher(
        steam=FakeSteam([complete({"AK": 120})]), skinport=FakeSkinport([{"AK": 100}])
    )
    cog, _ = price_cog(refresher)
    await cog.price_loop()
    assert refresher.book.get("AK") == Prices(120, 100)
    assert steam.calls == skinport.calls == 1


async def test_cog_without_price_updates_starts_no_loop() -> None:
    cog = Cs2(NullStats(), MagicMock(), resolver=NullResolver())  # type: ignore[arg-type]
    await cog.cog_load()
    assert not cog.price_loop.is_running()
    await cog.cog_unload()
