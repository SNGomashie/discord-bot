from __future__ import annotations

from ninja_bot.cs2.inventory import InventoryItem, ItemKind
from ninja_bot.cs2.match_skins import MATCH_SKINS_BUDGET_SECONDS, fetch_match_inventories
from ninja_bot.cs2.models import ApiError, Cs2Error, PrivateInventoryError, RateLimitedError

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
STEAM_C = "76561198000000003"
STEAM_D = "76561198000000004"


def item(name: str) -> InventoryItem:
    return InventoryItem(
        market_hash_name=name,
        name=name,
        item_type="Classified Rifle",
        marketable=True,
        colour=None,
        kind=ItemKind.SKIN,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeInventories:
    """Returns one item named after the id, or raises the configured error.

    Each call advances the clock by `step` seconds, like a paced real fetch.
    """

    def __init__(
        self,
        clock: FakeClock,
        errors: dict[str, Exception] | None = None,
        step: float = 4.0,
    ) -> None:
        self.clock = clock
        self.errors = errors or {}
        self.step = step
        self.calls: list[str] = []

    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]:
        self.calls.append(steam64_id)
        self.clock.now += self.step
        error = self.errors.get(steam64_id)
        if error is not None:
            raise error
        return [item(steam64_id)]

    async def aclose(self) -> None:
        pass


def test_budget_default() -> None:
    assert MATCH_SKINS_BUDGET_SECONDS == 30


async def test_all_public() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock)

    result = await fetch_match_inventories(provider, [STEAM_A, STEAM_B, STEAM_C], 30, clock)

    assert result == {
        STEAM_A: [item(STEAM_A)],
        STEAM_B: [item(STEAM_B)],
        STEAM_C: [item(STEAM_C)],
    }
    assert list(result) == [STEAM_A, STEAM_B, STEAM_C]
    assert provider.calls == [STEAM_A, STEAM_B, STEAM_C]


async def test_private_and_unavailable_are_skipped() -> None:
    clock = FakeClock()
    provider = FakeInventories(
        clock,
        errors={
            STEAM_A: PrivateInventoryError(),
            STEAM_C: ApiError(),
            STEAM_D: RuntimeError("boom"),
        },
    )

    result = await fetch_match_inventories(
        provider, [STEAM_A, STEAM_B, STEAM_C, STEAM_D], 30, clock
    )

    assert result == {STEAM_B: [item(STEAM_B)]}
    assert provider.calls == [STEAM_A, STEAM_B, STEAM_C, STEAM_D]


async def test_other_cs2_errors_are_skipped() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock, errors={STEAM_A: Cs2Error()})

    result = await fetch_match_inventories(provider, [STEAM_A, STEAM_B], 30, clock)

    assert result == {STEAM_B: [item(STEAM_B)]}


async def test_budget_exhausted_stops_new_fetches() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock, step=10.0)

    result = await fetch_match_inventories(
        provider, [STEAM_A, STEAM_B, STEAM_C, STEAM_D], 25, clock
    )

    # Fetches start at +0, +10 and +20; at +30 the 25 s budget is used up.
    assert provider.calls == [STEAM_A, STEAM_B, STEAM_C]
    assert list(result) == [STEAM_A, STEAM_B, STEAM_C]


async def test_budget_exactly_used_up_starts_nothing_more() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock, step=15.0)

    result = await fetch_match_inventories(provider, [STEAM_A, STEAM_B, STEAM_C], 30, clock)

    assert provider.calls == [STEAM_A, STEAM_B]
    assert list(result) == [STEAM_A, STEAM_B]


async def test_zero_budget_fetches_nothing() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock)

    assert await fetch_match_inventories(provider, [STEAM_A], 0, clock) == {}
    assert provider.calls == []


async def test_rate_limited_stops_immediately() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock, errors={STEAM_B: RateLimitedError()})

    result = await fetch_match_inventories(provider, [STEAM_A, STEAM_B, STEAM_C], 30, clock)

    assert result == {STEAM_A: [item(STEAM_A)]}
    assert provider.calls == [STEAM_A, STEAM_B]


async def test_duplicates_are_fetched_once_in_order() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock)

    result = await fetch_match_inventories(
        provider, [STEAM_B, STEAM_A, STEAM_B, STEAM_A, STEAM_C], 30, clock
    )

    assert provider.calls == [STEAM_B, STEAM_A, STEAM_C]
    assert list(result) == [STEAM_B, STEAM_A, STEAM_C]


async def test_no_players() -> None:
    clock = FakeClock()
    provider = FakeInventories(clock)

    assert await fetch_match_inventories(provider, [], 30, clock) == {}
    assert provider.calls == []
