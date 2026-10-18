from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, Protocol

log = logging.getLogger(__name__)

# How often the cog's loop checks what is due, and how old each source may get.
PRICE_CHECK_SECONDS = 600
SKINPORT_REFRESH_SECONDS = 3600
STEAM_REFRESH_SECONDS = 86400
# An incomplete Steam crawl (error or rate limit part-way) is retried sooner.
STEAM_RETRY_SECONDS = 3600


# Currencies both price sources support. Steam ids are the Steam Web API
# `ECurrencyCode` values (the `currency` parameter of `market/priceoverview` and
# `market/search/render`); Skinport codes are the `currency` values its
# `/v1/items` endpoint accepts (docs.skinport.com: AUD, BRL, CAD, CHF, CNY, CZK,
# DKK, EUR, GBP, HRK, NOK, PLN, RUB, SEK, TRY, USD). Only codes in both lists.
@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    steam_id: int
    skinport_code: str


CURRENCIES: dict[str, Currency] = {
    currency.code: currency
    for currency in (
        Currency("USD", "$", 1, "USD"),
        Currency("GBP", "£", 2, "GBP"),
        Currency("EUR", "€", 3, "EUR"),
        Currency("CHF", "CHF ", 4, "CHF"),
        Currency("CAD", "CA$", 20, "CAD"),
        Currency("AUD", "A$", 21, "AUD"),
    )
}

DEFAULT_CURRENCY = "EUR"
NO_PRICE = "n/a"


def format_price(minor: int | None, currency: Currency) -> str:
    """Minor units with the currency symbol (`€1,234.50`); None = `n/a`."""
    if minor is None:
        return NO_PRICE
    sign = "-" if minor < 0 else ""
    whole, cents = divmod(abs(minor), 100)
    return f"{sign}{currency.symbol}{whole:,}.{cents:02d}"


# The two price sources, also the `last_update` argument.
PriceSource = Literal["steam", "skinport"]


@dataclass(frozen=True)
class Prices:
    """One item's prices in minor units of the configured currency; None = no price."""

    steam: int | None = None
    skinport: int | None = None


class PriceStore(Protocol):
    async def load_prices(self) -> dict[str, Prices]: ...

    async def save_steam_prices(self, prices: Mapping[str, int], now: float) -> None:
        """Upserts Steam prices; names not in `prices` keep their old values."""
        ...

    async def save_skinport_prices(self, prices: Mapping[str, int], now: float) -> None:
        """Upserts Skinport prices; names not in `prices` keep their old values."""
        ...

    async def last_update(self, source: PriceSource) -> float | None:
        """When `source` was last saved (newest row), None if never."""
        ...


class PriceBook:
    """In-memory prices by `market_hash_name`, so valuing an inventory needs no I/O."""

    def __init__(self, prices: Mapping[str, Prices] | None = None) -> None:
        self._prices: dict[str, Prices] = dict(prices or {})

    def get(self, name: str) -> Prices | None:
        return self._prices.get(name)

    @property
    def is_empty(self) -> bool:
        return not self._prices

    @property
    def has_steam(self) -> bool:
        return any(prices.steam is not None for prices in self._prices.values())

    @property
    def has_skinport(self) -> bool:
        return any(prices.skinport is not None for prices in self._prices.values())

    def update_steam(self, mapping: Mapping[str, int]) -> None:
        for name, minor in mapping.items():
            self._prices[name] = replace(self._prices.get(name, Prices()), steam=minor)

    def update_skinport(self, mapping: Mapping[str, int]) -> None:
        for name, minor in mapping.items():
            self._prices[name] = replace(self._prices.get(name, Prices()), skinport=minor)


class SteamCrawl(Protocol):
    """What `PriceRefresher` reads from a Steam Market crawl (`steam_market.CrawlResult`)."""

    @property
    def prices(self) -> Mapping[str, int]: ...

    @property
    def complete(self) -> bool: ...


class SteamPriceSource(Protocol):
    """`steam_market.SteamMarketClient`; `crawl` never raises `Cs2Error`."""

    async def crawl(self) -> SteamCrawl: ...

    async def aclose(self) -> None: ...


class SkinportPriceSource(Protocol):
    """`skinport.SkinportClient`; `get_prices` raises `Cs2Error` on failure."""

    async def get_prices(self) -> dict[str, int]: ...

    async def aclose(self) -> None: ...


class PriceRefresher:
    """Keeps a `PriceBook` and the `PriceStore` fresh from both sources, independently.

    Skinport is due when never saved or older than `SKINPORT_REFRESH_SECONDS`; Steam when
    never saved or older than `STEAM_REFRESH_SECONDS`, and `STEAM_RETRY_SECONDS` after an
    incomplete crawl (whatever it gathered is saved). A failing source keeps its old prices.
    """

    def __init__(
        self,
        book: PriceBook,
        store: PriceStore,
        steam_client: SteamPriceSource,
        skinport_client: SkinportPriceSource,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.book = book
        self._store = store
        self._steam = steam_client
        self._skinport = skinport_client
        self._clock = clock
        self._skinport_updated: float | None = None
        # When the next Steam crawl is due; None = due now.
        self._steam_next: float | None = None

    async def load(self) -> None:
        """Fill the book from the store and take the last update times from it."""
        prices = await self._store.load_prices()
        self.book.update_steam({name: p.steam for name, p in prices.items() if p.steam is not None})
        self.book.update_skinport(
            {name: p.skinport for name, p in prices.items() if p.skinport is not None}
        )
        self._skinport_updated = await self._store.last_update("skinport")
        steam_updated = await self._store.last_update("steam")
        self._steam_next = None if steam_updated is None else steam_updated + STEAM_REFRESH_SECONDS

    def due(self) -> list[PriceSource]:
        """The sources to refresh now, Skinport first (one quick call before the long crawl)."""
        now = self._clock()
        sources: list[PriceSource] = []
        if (
            self._skinport_updated is None
            or now - self._skinport_updated >= SKINPORT_REFRESH_SECONDS
        ):
            sources.append("skinport")
        if self._steam_next is None or now >= self._steam_next:
            sources.append("steam")
        return sources

    async def refresh_skinport(self) -> None:
        """One bulk Skinport call; raises `Cs2Error` (nothing changed) when it fails."""
        prices = await self._skinport.get_prices()
        now = self._clock()
        self.book.update_skinport(prices)
        if prices:
            self._skinport_updated = now
        await self._store.save_skinport_prices(prices, now)

    async def refresh_steam(self) -> None:
        """Crawl the Steam Market and save what it got, even when the crawl stopped early."""
        result = await self._steam.crawl()
        now = self._clock()
        if result.complete and result.prices:
            self._steam_next = now + STEAM_REFRESH_SECONDS
        else:
            log.info("Steam Market crawl incomplete; retrying in %d s", STEAM_RETRY_SECONDS)
            self._steam_next = now + STEAM_RETRY_SECONDS
        self.book.update_steam(result.prices)
        await self._store.save_steam_prices(result.prices, now)

    async def run_cycle(self) -> None:
        """Refresh whatever is due; never raises, and one source failing never stops the other."""
        try:
            sources = self.due()
        except Exception as exc:
            log.warning("Could not check price refresh times (%s)", type(exc).__name__)
            return
        for source in sources:
            refresh = self.refresh_skinport if source == "skinport" else self.refresh_steam
            try:
                await refresh()
            except Exception as exc:
                log.warning("Refreshing %s prices failed (%s)", source, type(exc).__name__)

    async def aclose(self) -> None:
        """Close both clients, the second even if the first fails."""
        try:
            await self._steam.aclose()
        finally:
            await self._skinport.aclose()
