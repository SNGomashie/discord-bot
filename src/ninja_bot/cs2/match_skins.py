"""Inventories of everyone in a match, gathered within a time budget (no discord import)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from ninja_bot.cs2.inventory import InventoryItem, InventoryProvider
from ninja_bot.cs2.models import PrivateInventoryError, RateLimitedError

log = logging.getLogger(__name__)

# Cache misses are paced 4 s apart, so a cold 10-player match takes ~40 s in full.
MATCH_SKINS_BUDGET_SECONDS = 30.0


async def fetch_match_inventories(
    provider: InventoryProvider,
    steam_ids: Iterable[str],
    budget_seconds: float = MATCH_SKINS_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, list[InventoryItem]]:
    """Fetch each player's inventory in order; never raises.

    Ids are deduplicated (first occurrence wins). Private and otherwise unavailable
    inventories are skipped; a `RateLimitedError` stops at once. No new fetch starts
    once `budget_seconds` have passed, and whatever was fetched so far is returned.
    """
    deadline = clock() + budget_seconds
    inventories: dict[str, list[InventoryItem]] = {}
    for steam64_id in dict.fromkeys(steam_ids):
        if clock() >= deadline:
            log.debug("Match inventory budget used up; skipping the remaining players")
            break
        try:
            inventories[steam64_id] = await provider.get_inventory(steam64_id)
        except PrivateInventoryError:
            continue
        except RateLimitedError:
            log.info("Steam inventories are rate limited; skipping the remaining players")
            break
        except Exception as exc:
            log.warning("Skipping a match inventory: %s", type(exc).__name__)
    return inventories
