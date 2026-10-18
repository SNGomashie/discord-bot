from __future__ import annotations

import pytest

from ninja_bot.cs2.prices import (
    CURRENCIES,
    DEFAULT_CURRENCY,
    PriceBook,
    Prices,
    format_price,
)


def test_prices_default_to_none() -> None:
    assert Prices() == Prices(steam=None, skinport=None)


def test_empty_book() -> None:
    book = PriceBook()
    assert book.is_empty
    assert not book.has_steam
    assert not book.has_skinport
    assert book.get("AK-47 | Redline (Field-Tested)") is None


def test_book_from_loaded_prices() -> None:
    book = PriceBook({"A": Prices(steam=100)})
    assert not book.is_empty
    assert book.get("A") == Prices(steam=100)
    assert book.has_steam
    assert not book.has_skinport


def test_book_copies_its_input() -> None:
    loaded = {"A": Prices(steam=100)}
    book = PriceBook(loaded)
    book.update_steam({"B": 5})
    assert loaded == {"A": Prices(steam=100)}


def test_update_one_source_keeps_the_other() -> None:
    book = PriceBook()
    book.update_steam({"A": 100, "B": 200})
    book.update_skinport({"A": 90, "C": 30})
    book.update_steam({"A": 110})
    assert book.get("A") == Prices(steam=110, skinport=90)
    assert book.get("B") == Prices(steam=200)
    assert book.get("C") == Prices(skinport=30)
    assert book.has_steam
    assert book.has_skinport


def test_update_with_nothing_changes_nothing() -> None:
    book = PriceBook()
    book.update_steam({})
    book.update_skinport({})
    assert book.is_empty


def test_prices_are_frozen() -> None:
    prices = Prices(steam=1)
    with pytest.raises(AttributeError):
        prices.steam = 2  # type: ignore[misc]


def test_currency_table() -> None:
    assert {"USD", "EUR", "GBP"} <= set(CURRENCIES)
    assert DEFAULT_CURRENCY in CURRENCIES
    for code, currency in CURRENCIES.items():
        assert currency.code == code == currency.skinport_code
    assert CURRENCIES["USD"].steam_id == 1
    assert CURRENCIES["GBP"].steam_id == 2
    assert CURRENCIES["EUR"].steam_id == 3
    assert len({currency.steam_id for currency in CURRENCIES.values()}) == len(CURRENCIES)


@pytest.mark.parametrize(
    ("minor", "code", "expected"),
    [
        (123450, "EUR", "€1,234.50"),
        (0, "EUR", "€0.00"),
        (5, "USD", "$0.05"),
        (99, "GBP", "£0.99"),
        (123456789, "USD", "$1,234,567.89"),
        (-250, "EUR", "-€2.50"),
        (1000, "CHF", "CHF 10.00"),
    ],
)
def test_format_price(minor: int, code: str, expected: str) -> None:
    assert format_price(minor, CURRENCIES[code]) == expected


def test_format_price_none() -> None:
    assert format_price(None, CURRENCIES["EUR"]) == "n/a"
