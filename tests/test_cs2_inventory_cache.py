from __future__ import annotations

import asyncio

import pytest

from ninja_bot.cs2.inventory import InventoryItem, InventoryProvider, ItemKind
from ninja_bot.cs2.inventory_cache import (
    INVENTORY_TTL_SECONDS,
    MIN_FETCH_INTERVAL_SECONDS,
    PRIVATE_TTL_SECONDS,
    RATE_LIMIT_BACKOFF_SECONDS,
    CachedInventoryProvider,
)
from ninja_bot.cs2.inventory_client import INVENTORY_RATE_LIMITED_MESSAGE
from ninja_bot.cs2.models import ApiError, PrivateInventoryError, RateLimitedError

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
STEAM_C = "76561198000000003"

ITEM = InventoryItem(
    market_hash_name="AK-47 | Redline (Field-Tested)",
    name="AK-47 | Redline",
    item_type="Classified Rifle",
    marketable=True,
    colour=0xD32CE6,
    kind=ItemKind.SKIN,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSleep:
    """Records sleeps and advances the clock, like a real sleep would."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.now += seconds


class FakeInventories:
    def __init__(self, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.calls: list[tuple[str, float]] = []
        self.closed = False
        self.clock: FakeClock | None = None

    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]:
        self.calls.append((steam64_id, self.clock.now if self.clock else 0.0))
        error = self.errors.get(steam64_id)
        if error is not None:
            raise error
        return [ITEM]

    async def aclose(self) -> None:
        self.closed = True


def make_provider(
    inner: FakeInventories,
) -> tuple[CachedInventoryProvider, FakeClock, FakeSleep]:
    clock = FakeClock()
    inner.clock = clock
    sleep = FakeSleep(clock)
    return CachedInventoryProvider(inner, clock=clock, sleep=sleep), clock, sleep


def test_satisfies_protocol() -> None:
    provider: InventoryProvider = CachedInventoryProvider(FakeInventories())
    assert provider is not None


async def test_miss_then_hit() -> None:
    inner = FakeInventories()
    provider, _, sleep = make_provider(inner)

    assert await provider.get_inventory(STEAM_A) == [ITEM]
    assert await provider.get_inventory(STEAM_A) == [ITEM]

    assert [steam_id for steam_id, _ in inner.calls] == [STEAM_A]
    assert sleep.calls == []


async def test_entry_expires_after_ttl() -> None:
    inner = FakeInventories()
    provider, clock, _ = make_provider(inner)

    await provider.get_inventory(STEAM_A)
    clock.now += INVENTORY_TTL_SECONDS - 1
    await provider.get_inventory(STEAM_A)
    assert len(inner.calls) == 1

    clock.now += 1
    await provider.get_inventory(STEAM_A)
    assert len(inner.calls) == 2


async def test_misses_are_spaced_out() -> None:
    inner = FakeInventories()
    provider, clock, sleep = make_provider(inner)

    await provider.get_inventory(STEAM_A)
    clock.now += 1
    await provider.get_inventory(STEAM_B)

    assert sleep.calls == [MIN_FETCH_INTERVAL_SECONDS - 1]
    (_, first), (_, second) = inner.calls
    assert second - first == MIN_FETCH_INTERVAL_SECONDS


async def test_no_wait_once_interval_passed() -> None:
    inner = FakeInventories()
    provider, clock, sleep = make_provider(inner)

    await provider.get_inventory(STEAM_A)
    clock.now += MIN_FETCH_INTERVAL_SECONDS
    await provider.get_inventory(STEAM_B)

    assert sleep.calls == []


async def test_hits_never_wait() -> None:
    inner = FakeInventories()
    provider, _, sleep = make_provider(inner)

    await provider.get_inventory(STEAM_A)
    await provider.get_inventory(STEAM_A)

    assert sleep.calls == []


async def test_concurrent_misses_are_serialised_and_deduplicated() -> None:
    inner = FakeInventories()
    provider, _, sleep = make_provider(inner)

    results = await asyncio.gather(
        provider.get_inventory(STEAM_A),
        provider.get_inventory(STEAM_B),
        provider.get_inventory(STEAM_A),
    )

    assert results == [[ITEM], [ITEM], [ITEM]]
    assert [steam_id for steam_id, _ in inner.calls] == [STEAM_A, STEAM_B]
    assert sleep.calls == [MIN_FETCH_INTERVAL_SECONDS]


async def test_private_inventory_is_negatively_cached() -> None:
    inner = FakeInventories({STEAM_A: PrivateInventoryError()})
    provider, clock, _ = make_provider(inner)

    for _ in range(2):
        with pytest.raises(PrivateInventoryError) as excinfo:
            await provider.get_inventory(STEAM_A)
        assert excinfo.value.message == PrivateInventoryError.default_message
    assert len(inner.calls) == 1

    clock.now += PRIVATE_TTL_SECONDS
    with pytest.raises(PrivateInventoryError):
        await provider.get_inventory(STEAM_A)
    assert len(inner.calls) == 2


async def test_other_errors_are_not_cached() -> None:
    inner = FakeInventories({STEAM_A: ApiError()})
    provider, _, _ = make_provider(inner)

    for _ in range(2):
        with pytest.raises(ApiError):
            await provider.get_inventory(STEAM_A)

    assert len(inner.calls) == 2


async def test_rate_limit_backs_off() -> None:
    inner = FakeInventories({STEAM_A: RateLimitedError(INVENTORY_RATE_LIMITED_MESSAGE)})
    provider, clock, _ = make_provider(inner)

    with pytest.raises(RateLimitedError):
        await provider.get_inventory(STEAM_A)
    clock.now += RATE_LIMIT_BACKOFF_SECONDS - 1
    with pytest.raises(RateLimitedError) as excinfo:
        await provider.get_inventory(STEAM_B)
    assert excinfo.value.message == INVENTORY_RATE_LIMITED_MESSAGE
    assert [steam_id for steam_id, _ in inner.calls] == [STEAM_A]

    clock.now += 1
    assert await provider.get_inventory(STEAM_B) == [ITEM]
    assert [steam_id for steam_id, _ in inner.calls] == [STEAM_A, STEAM_B]


async def test_backoff_still_serves_cache_hits() -> None:
    inner = FakeInventories({STEAM_B: RateLimitedError()})
    provider, clock, _ = make_provider(inner)

    await provider.get_inventory(STEAM_A)
    clock.now += MIN_FETCH_INTERVAL_SECONDS
    with pytest.raises(RateLimitedError):
        await provider.get_inventory(STEAM_B)

    assert await provider.get_inventory(STEAM_A) == [ITEM]
    with pytest.raises(RateLimitedError):
        await provider.get_inventory(STEAM_C)
    assert len(inner.calls) == 2


async def test_aclose_delegates() -> None:
    inner = FakeInventories()
    provider, _, _ = make_provider(inner)

    await provider.aclose()

    assert inner.closed
