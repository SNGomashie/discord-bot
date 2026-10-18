import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.cs2.inventory import (
    MAX_STACK_AMOUNT,
    InventoryItem,
    ItemKind,
    item_kind,
    parse_inventory,
)
from ninja_bot.cs2.models import ApiError, Cs2Error, PrivateInventoryError

FIXTURE = Path(__file__).parent / "fixtures" / "steam" / "inventory.json"


@pytest.fixture
def raw_inventory() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def _by_name(items: list[InventoryItem]) -> dict[str, InventoryItem]:
    return {item.market_hash_name: item for item in items}


def _payload(asset: dict[str, Any] | None = None, **description: Any) -> dict[str, Any]:
    return {
        "assets": [{"classid": "1", "instanceid": "0", "amount": "1", **(asset or {})}],
        "descriptions": [
            {"classid": "1", "instanceid": "0", "market_hash_name": "Thing", **description}
        ],
        "success": 1,
    }


def test_full_fixture(raw_inventory: dict[str, Any]) -> None:
    items = parse_inventory(raw_inventory)

    assert len(items) == 10
    redline = _by_name(items)["AK-47 | Redline (Field-Tested)"]
    assert redline == InventoryItem(
        market_hash_name="AK-47 | Redline (Field-Tested)",
        name="AK-47 | Redline",
        item_type="Classified Rifle",
        marketable=True,
        colour=0xD32CE6,
        kind=ItemKind.SKIN,
    )


def test_kinds(raw_inventory: dict[str, Any]) -> None:
    kinds = {name: item.kind for name, item in _by_name(parse_inventory(raw_inventory)).items()}

    assert kinds == {
        "AK-47 | Redline (Field-Tested)": ItemKind.SKIN,
        "★ Karambit | Fade (Factory New)": ItemKind.SKIN,
        "★ Sport Gloves | Vice (Minimal Wear)": ItemKind.SKIN,
        "Sticker | Crown (Foil)": ItemKind.OTHER,
        "Kilowatt Case": ItemKind.OTHER,
        "Revolution Case": ItemKind.OTHER,
        "5 Year Veteran Coin": ItemKind.OTHER,
    }


def test_stacks_give_one_item_per_unit(raw_inventory: dict[str, Any]) -> None:
    counts = Counter(item.market_hash_name for item in parse_inventory(raw_inventory))

    assert counts["Revolution Case"] == 3  # one asset with amount 3
    assert counts["Kilowatt Case"] == 2  # two assets sharing a description
    assert counts["AK-47 | Redline (Field-Tested)"] == 1


def test_non_marketable(raw_inventory: dict[str, Any]) -> None:
    items = _by_name(parse_inventory(raw_inventory))

    assert items["5 Year Veteran Coin"].marketable is False
    assert items["Kilowatt Case"].marketable is True


def test_join_uses_instanceid() -> None:
    payload = _payload({"instanceid": "7"})
    payload["descriptions"].append(
        {"classid": "1", "instanceid": "7", "market_hash_name": "Right one"}
    )

    assert [item.market_hash_name for item in parse_inventory(payload)] == ["Right one"]


def test_asset_without_description_is_skipped() -> None:
    assert parse_inventory(_payload({"classid": "2"})) == []


def test_missing_instanceid_defaults_to_zero() -> None:
    payload = _payload()
    del payload["assets"][0]["instanceid"]
    del payload["descriptions"][0]["instanceid"]

    assert len(parse_inventory(payload)) == 1


@pytest.mark.parametrize("payload", [None, {"total_inventory_count": 0, "success": 1}])
def test_empty_inventory(payload: Any) -> None:
    assert parse_inventory(payload) == []


def test_empty_assets_without_descriptions() -> None:
    assert parse_inventory({"assets": [], "success": 1}) == []


@pytest.mark.parametrize(
    "item_type,expected",
    [
        ("Covert Rifle", ItemKind.SKIN),
        ("Classified Sniper Rifle", ItemKind.SKIN),
        ("Mil-Spec Grade Pistol", ItemKind.SKIN),
        ("Restricted SMG", ItemKind.SKIN),
        ("Industrial Grade Shotgun", ItemKind.SKIN),
        ("Consumer Grade Machinegun", ItemKind.SKIN),
        ("★ Covert Knife", ItemKind.SKIN),
        ("★ Extraordinary Gloves", ItemKind.SKIN),
        ("High Grade Sticker", ItemKind.OTHER),
        ("Base Grade Container", ItemKind.OTHER),
        ("High Grade Music Kit", ItemKind.OTHER),
        ("Base Grade Graffiti", ItemKind.OTHER),
        ("Remarkable Charm", ItemKind.OTHER),
        ("Superior Agent", ItemKind.OTHER),
        ("", ItemKind.OTHER),
    ],
)
def test_kind_fallback_on_type(item_type: str, expected: ItemKind) -> None:
    assert item_kind(None, item_type) is expected
    assert (
        item_kind([{"category": "Weapon", "internal_name": "weapon_ak47"}], item_type) is expected
    )


def test_type_tag_wins_over_type_text() -> None:
    tags = [{"category": "Type", "internal_name": "CSGO_Tool_Sticker"}]

    assert item_kind(tags, "Covert Rifle") is ItemKind.OTHER


def test_name_and_colour_fallbacks() -> None:
    item = parse_inventory(_payload(name_color="4B69FF"))[0]

    assert item.name == "Thing"
    assert item.item_type == ""
    assert item.colour == 0x4B69FF
    assert item.marketable is False
    assert item.kind is ItemKind.OTHER


@pytest.mark.parametrize("colour", ["zzzzzz", "+12345", "123", 123])
def test_bad_colour_is_none(colour: Any) -> None:
    assert parse_inventory(_payload(name_color=colour))[0].colour is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "nope",
        {"success": False},
        {"success": 0, "assets": []},
        {"assets": {}, "descriptions": []},
        {"assets": [], "descriptions": {}},
        {"assets": ["x"], "descriptions": []},
        {"assets": [], "descriptions": ["x"]},
        {"assets": [{"instanceid": "0"}], "descriptions": []},
        _payload(market_hash_name=None),
        _payload(market_hash_name="  "),
        _payload({"amount": "0"}),
        _payload({"amount": "-1"}),
        _payload({"amount": "many"}),
        _payload({"amount": True}),
        _payload({"amount": str(MAX_STACK_AMOUNT + 1)}),
    ],
)
def test_malformed_raises_api_error(payload: Any) -> None:
    with pytest.raises(ApiError) as error:
        parse_inventory(payload)
    assert "Steam" in error.value.message


def test_private_inventory_error() -> None:
    error = PrivateInventoryError()

    assert isinstance(error, Cs2Error)
    assert "private" in error.message
