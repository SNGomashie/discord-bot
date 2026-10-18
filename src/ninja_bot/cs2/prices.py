from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal, Protocol


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
