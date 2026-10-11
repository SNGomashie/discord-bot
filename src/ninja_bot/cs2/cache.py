"""Short-lived in-memory caching, so repeated leaderboard calls don't burn API quota."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable

from ninja_bot.cs2.client import StatsProvider
from ninja_bot.cs2.models import Match, Profile

PROFILE_TTL_SECONDS = 300.0


class TtlCache[K: Hashable, V]:
    """Entries expire `ttl_seconds` after `set`; the oldest is evicted past `max_size`."""

    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_size: int = 128,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._clock = clock
        # key -> (expires_at, value), oldest insertion first.
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: K, value: V) -> None:
        self._entries.pop(key, None)
        self._entries[key] = (self._clock() + self.ttl_seconds, value)
        while len(self._entries) > self.max_size:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


class CachedStatsProvider:
    """`StatsProvider` that caches `get_profile` per Steam ID; everything else passes through."""

    def __init__(
        self,
        inner: StatsProvider,
        ttl_seconds: float = PROFILE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._profiles: TtlCache[str, Profile] = TtlCache(ttl_seconds, clock)

    async def get_profile(self, steam64_id: str) -> Profile:
        cached = self._profiles.get(steam64_id)
        if cached is not None:
            return cached
        # Errors propagate before `set`, so they are never cached.
        profile = await self._inner.get_profile(steam64_id)
        self._profiles.set(steam64_id, profile)
        return profile

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        return await self._inner.get_recent_matches(steam64_id)

    async def get_match(self, game_id: str) -> Match:
        return await self._inner.get_match(game_id)

    async def aclose(self) -> None:
        await self._inner.aclose()
