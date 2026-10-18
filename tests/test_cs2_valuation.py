from __future__ import annotations

from ninja_bot.cs2.inventory import InventoryItem, ItemKind
from ninja_bot.cs2.prices import PriceBook, Prices
from ninja_bot.cs2.valuation import (
    DEFAULT_MATCH_TOP,
    Valuation,
    ValuedItem,
    match_top_skins,
    value_inventory,
)


def item(name: str, kind: ItemKind = ItemKind.SKIN, *, marketable: bool = True) -> InventoryItem:
    return InventoryItem(
        market_hash_name=name,
        name=name,
        item_type="Covert Rifle" if kind is ItemKind.SKIN else "Base Grade Container",
        marketable=marketable,
        colour=None,
        kind=kind,
    )


def book(**prices: Prices) -> PriceBook:
    return PriceBook(prices)


def test_empty_inventory() -> None:
    assert value_inventory([], PriceBook(), 5) == Valuation(0, 0, 0, 0, ())
    assert match_top_skins({}, PriceBook()) == []
    assert match_top_skins({"1": [], "2": []}, PriceBook()) == []


def test_stacks_are_grouped_and_counted() -> None:
    prices = book(Case=Prices(steam=50, skinport=40), AK=Prices(steam=1000, skinport=900))
    items = [item("Case", ItemKind.OTHER)] * 3 + [item("AK"), item("AK")]
    valuation = value_inventory(items, prices, 5)
    assert valuation.steam_total == 3 * 50 + 2 * 1000
    assert valuation.skinport_total == 3 * 40 + 2 * 900
    assert valuation.item_count == 5
    assert valuation.priced_count == 5
    assert valuation.top == (ValuedItem("AK", 2, Prices(steam=1000, skinport=900)),)


def test_non_marketable_items_are_ignored() -> None:
    prices = book(AK=Prices(steam=1000), Medal=Prices(steam=99999))
    items = [item("AK"), item("Medal", marketable=False), item("AK", marketable=False)]
    valuation = value_inventory(items, prices, 5)
    assert valuation.steam_total == 1000
    assert valuation.item_count == 1
    assert [v.name for v in valuation.top] == ["AK"]
    assert valuation.top[0].count == 1


def test_one_source_only_contributes_zero_to_the_other() -> None:
    prices = book(A=Prices(steam=300), B=Prices(skinport=200))
    valuation = value_inventory([item("A"), item("B")], prices, 5)
    assert valuation.steam_total == 300
    assert valuation.skinport_total == 200
    assert valuation.priced_count == 2


def test_unpriced_items_are_counted_but_not_ranked() -> None:
    prices = book(A=Prices(steam=300), Empty=Prices())
    items = [item("A"), item("Unknown"), item("Unknown"), item("Empty")]
    valuation = value_inventory(items, prices, 5)
    assert valuation.item_count == 4
    assert valuation.priced_count == 1
    assert valuation.item_count - valuation.priced_count == 3
    assert [v.name for v in valuation.top] == ["A"]
    assert valuation.steam_total == 300


def test_top_holds_skins_only() -> None:
    prices = book(Sticker=Prices(steam=50000), AK=Prices(steam=1000))
    valuation = value_inventory([item("Sticker", ItemKind.OTHER), item("AK")], prices, 5)
    assert [v.name for v in valuation.top] == ["AK"]
    assert valuation.steam_total == 51000


def test_top_ranks_by_steam_falling_back_to_skinport() -> None:
    prices = book(
        A=Prices(steam=100, skinport=5000),
        B=Prices(skinport=300),
        C=Prices(steam=200, skinport=10),
    )
    valuation = value_inventory([item("A"), item("B"), item("C")], prices, 5)
    assert [v.name for v in valuation.top] == ["B", "C", "A"]


def test_top_ranks_by_unit_price_not_stack_worth() -> None:
    prices = book(Cheap=Prices(steam=100), Dear=Prices(steam=500))
    valuation = value_inventory([item("Cheap")] * 10 + [item("Dear")], prices, 5)
    assert [v.name for v in valuation.top] == ["Dear", "Cheap"]


def test_ties_are_broken_by_name() -> None:
    prices = book(Zeta=Prices(steam=100), Alpha=Prices(skinport=100), Mid=Prices(steam=100))
    valuation = value_inventory([item("Zeta"), item("Mid"), item("Alpha")], prices, 5)
    assert [v.name for v in valuation.top] == ["Alpha", "Mid", "Zeta"]


def test_top_is_capped() -> None:
    prices = book(**{f"S{n}": Prices(steam=n) for n in range(1, 8)})
    valuation = value_inventory([item(f"S{n}") for n in range(1, 8)], prices, 3)
    assert [v.name for v in valuation.top] == ["S7", "S6", "S5"]
    assert value_inventory([item("S1")], prices, 0).top == ()


def test_match_top_skins_across_inventories_with_owners() -> None:
    prices = book(
        Knife=Prices(steam=50000),
        AK=Prices(steam=1000, skinport=900),
        AWP=Prices(skinport=2000),
        Case=Prices(steam=99999),
        M4=Prices(steam=10),
    )
    inventories = {
        "111": [item("AK"), item("AK"), item("Case", ItemKind.OTHER)],
        "222": [item("Knife"), item("M4"), item("Nothing")],
        "333": [item("AWP"), item("AK", marketable=False)],
    }
    top = match_top_skins(inventories, prices)
    assert len(top) == DEFAULT_MATCH_TOP
    assert top == [
        ValuedItem("Knife", 1, Prices(steam=50000), owner="222"),
        ValuedItem("AWP", 1, Prices(skinport=2000), owner="333"),
        ValuedItem("AK", 2, Prices(steam=1000, skinport=900), owner="111"),
    ]


def test_match_top_skins_same_item_tie_broken_by_owner() -> None:
    prices = book(AK=Prices(steam=1000))
    top = match_top_skins({"222": [item("AK")], "111": [item("AK")]}, prices, top_n=5)
    assert [v.owner for v in top] == ["111", "222"]
