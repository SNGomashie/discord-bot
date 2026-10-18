"""Cached, rate-paced inventory lookups, so Steam's per-IP inventory limit is respected."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ninja_bot.cs2.cache import TtlCache
from ninja_bot.cs2.inventory import InventoryItem, InventoryProvider
from ninja_bot.cs2.inventory_client import INVENTORY_RATE_LIMITED_MESSAGE
from ninja_bot.cs2.models import PrivateInventoryError, RateLimitedError

INVENTORY_TTL_SECONDS = 1800.0
# Private inventories are remembered briefly, so a match full of private players
# doesn't re-fetch each of them on every lookup.
PRIVATE_TTL_SECONDS = 600.0
MIN_FETCH_INTERVAL_SECONDS = 4.0
RATE_LIMIT_BACKOFF_SECONDS = 300.0

SleepFn = Callable[[float], Awaitable[None]]


class CachedInventoryProvider:
    """`InventoryProvider` that caches inventories and spaces out network fetches.

    Successful lookups are cached for `INVENTORY_TTL_SECONDS`, private inventories for
    `PRIVATE_TTL_SECONDS`; other errors are never cached. Cache misses are serialised
    and start at least `MIN_FETCH_INTERVAL_SECONDS` apart; cache hits never wait. After
    a `RateLimitedError` every miss fails fast for `RATE_LIMIT_BACKOFF_SECONDS`.
    """

    def __init__(
        self,
        inner: InventoryProvider,
        clock: Callable[[], float] = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._sleep = sleep
        self._inventories: TtlCache[str, list[InventoryItem]] = TtlCache(
            INVENTORY_TTL_SECONDS, clock
        )
        # Steam ID -> the private error's message (a negative entry).
        self._private: TtlCache[str, str] = TtlCache(PRIVATE_TTL_SECONDS, clock)
        self._lock = asyncio.Lock()
        self._last_fetch: float | None = None
        self._backoff_until: float | None = None

    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]:
        cached = self._cached(steam64_id)
        if cached is not None:
            return cached
        self._check_backoff()
        async with self._lock:
            # Another caller may have fetched it (or hit the rate limit) while we waited.
            cached = self._cached(steam64_id)
            if cached is not None:
                return cached
            self._check_backoff()
            await self._wait_for_slot()
            self._last_fetch = self._clock()
            try:
                items = await self._inner.get_inventory(steam64_id)
            except PrivateInventoryError as error:
                self._private.set(steam64_id, error.message)
                raise
            except RateLimitedError:
                self._backoff_until = self._clock() + RATE_LIMIT_BACKOFF_SECONDS
                raise
            self._inventories.set(steam64_id, items)
            return items

    async def aclose(self) -> None:
        await self._inner.aclose()

    def _cached(self, steam64_id: str) -> list[InventoryItem] | None:
        """The cached inventory; raises the cached `PrivateInventoryError` if any."""
        private_message = self._private.get(steam64_id)
        if private_message is not None:
            raise PrivateInventoryError(private_message)
        return self._inventories.get(steam64_id)

    def _check_backoff(self) -> None:
        if self._backoff_until is None:
            return
        if self._clock() < self._backoff_until:
            raise RateLimitedError(INVENTORY_RATE_LIMITED_MESSAGE)
        self._backoff_until = None

    async def _wait_for_slot(self) -> None:
        if self._last_fetch is None:
            return
        remaining = self._last_fetch + MIN_FETCH_INTERVAL_SECONDS - self._clock()
        if remaining > 0:
            await self._sleep(remaining)
