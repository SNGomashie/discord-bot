"""Inventory worth and top skins from a `PriceBook`; pure, no network."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ninja_bot.cs2.inventory import InventoryItem, ItemKind
from ninja_bot.cs2.prices import PriceBook, Prices

DEFAULT_MATCH_TOP = 3


@dataclass(frozen=True)
class ValuedItem:
    """One stack of an item (`name` = `market_hash_name`) with its unit prices."""

    name: str
    count: int
    prices: Prices
    owner: str | None = None  # steam64 id, set by `match_top_skins`


@dataclass(frozen=True)
class Valuation:
    """Totals in minor units over marketable items; counts are in units, not stacks."""

    steam_total: int
    skinport_total: int
    item_count: int
    priced_count: int
    top: tuple[ValuedItem, ...]


def value_inventory(items: Iterable[InventoryItem], book: PriceBook, top_n: int) -> Valuation:
    """Worth of every marketable item (price x count per source, missing = 0) and the
    `top_n` priciest skins (weapons, knives, gloves) with a price."""
    stacks = _stacks(items, book)
    steam_total = 0
    skinport_total = 0
    item_count = 0
    priced_count = 0
    for valued, _ in stacks:
        item_count += valued.count
        if _rank_price(valued.prices) is not None:
            priced_count += valued.count
        steam_total += (valued.prices.steam or 0) * valued.count
        skinport_total += (valued.prices.skinport or 0) * valued.count
    skins = [valued for valued, kind in stacks if kind is ItemKind.SKIN]
    return Valuation(
        steam_total=steam_total,
        skinport_total=skinport_total,
        item_count=item_count,
        priced_count=priced_count,
        top=tuple(_ranked(skins)[: max(top_n, 0)]),
    )


def match_top_skins(
    inventories: Mapping[str, list[InventoryItem]],
    book: PriceBook,
    top_n: int = DEFAULT_MATCH_TOP,
) -> list[ValuedItem]:
    """The `top_n` priciest skins across inventories keyed by steam64 id, with owners."""
    skins = [
        ValuedItem(valued.name, valued.count, valued.prices, owner=owner)
        for owner, items in inventories.items()
        for valued, kind in _stacks(items, book)
        if kind is ItemKind.SKIN
    ]
    return _ranked(skins)[: max(top_n, 0)]


def _stacks(items: Iterable[InventoryItem], book: PriceBook) -> list[tuple[ValuedItem, ItemKind]]:
    counts: Counter[str] = Counter()
    kinds: dict[str, ItemKind] = {}
    for item in items:
        if not item.marketable:
            continue
        counts[item.market_hash_name] += 1
        kinds.setdefault(item.market_hash_name, item.kind)
    return [
        (ValuedItem(name, count, book.get(name) or Prices()), kinds[name])
        for name, count in counts.items()
    ]


def _rank_price(prices: Prices) -> int | None:
    return prices.steam if prices.steam is not None else prices.skinport


def _ranked(items: list[ValuedItem]) -> list[ValuedItem]:
    """Priced items only, by Steam price (Skinport fallback) desc, then name, then owner."""
    priced = [(price, item) for item in items if (price := _rank_price(item.prices)) is not None]
    priced.sort(key=lambda pair: (-pair[0], pair[1].name, pair[1].owner or ""))
    return [item for _, item in priced]
