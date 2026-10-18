"""Steam inventory items (CS2, appid 730 context 2) and their parsing; no network."""

from __future__ import annotations

import enum
import string
from dataclasses import dataclass
from typing import Any, Protocol

from ninja_bot.cs2.models import ApiError

INVENTORY_ERROR_MESSAGE = "Steam sent an inventory we couldn't read. Try again later."

# Larger asset `amount`s are treated as malformed (items are expanded one per unit).
MAX_STACK_AMOUNT = 10_000

# `Type` tag internal names of weapons, knives and gloves.
SKIN_TYPE_TAGS = frozenset(
    {
        "CSGO_Type_Pistol",
        "CSGO_Type_SMG",
        "CSGO_Type_Rifle",
        "CSGO_Type_SniperRifle",
        "CSGO_Type_Shotgun",
        "CSGO_Type_Machinegun",
        "CSGO_Type_Knife",
        "Type_Hands",
    }
)

# Fallback without a `Type` tag: the last word(s) of `type`, e.g. "Covert Rifle",
# "★ Covert Knife", "★ Extraordinary Gloves", "Classified Sniper Rifle".
SKIN_TYPE_WORDS = frozenset({"pistol", "smg", "rifle", "shotgun", "machinegun", "knife", "gloves"})


class ItemKind(enum.Enum):
    SKIN = "skin"  # weapons, knives and gloves
    OTHER = "other"  # stickers, cases, charms, graffiti, music kits, agents, ...


@dataclass(frozen=True)
class InventoryItem:
    market_hash_name: str
    name: str
    item_type: str
    marketable: bool
    colour: int | None
    kind: ItemKind


class InventoryProvider(Protocol):
    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]: ...

    # Wrappers (such as a cache around another provider) must delegate this.
    async def aclose(self) -> None: ...


def parse_inventory(payload: Any) -> list[InventoryItem]:
    """Items of a Steam inventory response, one per unit (a stack of n gives n items).

    Assets are joined to `descriptions` by `classid` + `instanceid`; assets without a
    description are skipped. A `null` payload or one without assets is an empty
    inventory. Anything else unexpected raises `ApiError`.
    """
    if payload is None:
        return []
    if not isinstance(payload, dict):
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    success = payload.get("success", 1)
    if success is False or success == 0:
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    assets = payload.get("assets")
    if assets is None:
        return []
    descriptions = payload.get("descriptions")
    if descriptions is None:
        descriptions = []
    if not isinstance(assets, list) or not isinstance(descriptions, list):
        raise ApiError(INVENTORY_ERROR_MESSAGE)

    items_by_key: dict[tuple[str, str], InventoryItem] = {}
    for raw in descriptions:
        if not isinstance(raw, dict):
            raise ApiError(INVENTORY_ERROR_MESSAGE)
        items_by_key[_key(raw)] = _parse_description(raw)

    items: list[InventoryItem] = []
    for raw in assets:
        if not isinstance(raw, dict):
            raise ApiError(INVENTORY_ERROR_MESSAGE)
        item = items_by_key.get(_key(raw))
        if item is not None:
            items.extend([item] * _amount(raw.get("amount", 1)))
    return items


def item_kind(tags: Any, item_type: str) -> ItemKind:
    """`SKIN` for weapons, knives and gloves by the `Type` tag; else by the `type` text."""
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("category") == "Type":
                internal = tag.get("internal_name")
                if isinstance(internal, str):
                    return ItemKind.SKIN if internal in SKIN_TYPE_TAGS else ItemKind.OTHER
    words = item_type.lower().split()
    return ItemKind.SKIN if words and words[-1] in SKIN_TYPE_WORDS else ItemKind.OTHER


def _parse_description(raw: dict[str, Any]) -> InventoryItem:
    market_hash_name = raw.get("market_hash_name")
    if not isinstance(market_hash_name, str) or not market_hash_name.strip():
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    name = raw.get("name")
    item_type = raw.get("type")
    tags = raw.get("tags")
    item_type = item_type if isinstance(item_type, str) else ""
    return InventoryItem(
        market_hash_name=market_hash_name,
        name=name if isinstance(name, str) and name.strip() else market_hash_name,
        item_type=item_type,
        marketable=_flag(raw.get("marketable")),
        colour=_rarity_colour(tags) or _hex_colour(raw.get("name_color")),
        kind=item_kind(tags, item_type),
    )


def _key(raw: dict[str, Any]) -> tuple[str, str]:
    classid = _id(raw.get("classid"))
    if classid is None:
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    return classid, _id(raw.get("instanceid")) or "0"


def _id(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return value.strip()
    return None


def _amount(value: Any) -> int:
    amount = _id(value)
    if amount is None or not 1 <= int(amount) <= MAX_STACK_AMOUNT:
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    return int(amount)


def _flag(value: Any) -> bool:
    return value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1)


def _rarity_colour(tags: Any) -> int | None:
    if not isinstance(tags, list):
        return None
    for tag in tags:
        if isinstance(tag, dict) and tag.get("category") == "Rarity":
            return _hex_colour(tag.get("color"))
    return None


def _hex_colour(value: Any) -> int | None:
    if isinstance(value, str) and len(value) == 6 and all(c in string.hexdigits for c in value):
        return int(value, 16)
    return None
