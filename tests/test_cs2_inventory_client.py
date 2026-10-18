import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from ninja_bot.cs2.client import FetchResponse
from ninja_bot.cs2.inventory import INVENTORY_ERROR_MESSAGE, InventoryProvider, ItemKind
from ninja_bot.cs2.inventory_client import (
    INVENTORY_RATE_LIMITED_MESSAGE,
    MAX_INVENTORY_BYTES,
    SteamInventoryClient,
    inventory_url,
)
from ninja_bot.cs2.models import (
    ApiError,
    InvalidSteamIdError,
    PrivateInventoryError,
    RateLimitedError,
)
from ninja_bot.cs2.resolver import MAX_BODY_BYTES, READ_CHUNK_BYTES

FIXTURE = (Path(__file__).parent / "fixtures" / "steam" / "inventory.json").read_text()
STEAM_ID = "76561197960287930"
EXPECTED_URL = f"https://steamcommunity.com/inventory/{STEAM_ID}/730/2?l=english&count=2000"


class StubFetch:
    def __init__(self, status: int = 200, body: str = FIXTURE) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.error: BaseException | None = None
        self.delay_s = 0.0

    async def __call__(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        self.calls.append((url, dict(headers)))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return FetchResponse(self.status, self.body)


def test_client_satisfies_protocol() -> None:
    client: InventoryProvider = SteamInventoryClient()
    assert isinstance(client, SteamInventoryClient)


def test_cap_is_larger_than_resolver_cap() -> None:
    assert MAX_INVENTORY_BYTES > MAX_BODY_BYTES


async def test_success_requests_exact_url_and_parses_items() -> None:
    fetch = StubFetch()
    items = await SteamInventoryClient(fetch).get_inventory(STEAM_ID)
    [(url, headers)] = fetch.calls
    assert url == EXPECTED_URL
    assert headers["Accept"] == "application/json"
    assert items
    assert {item.kind for item in items} == {ItemKind.SKIN, ItemKind.OTHER}


@pytest.mark.parametrize("body", ["null", '{"success": 1, "total_inventory_count": 0}'])
async def test_empty_inventory_is_empty_list(body: str) -> None:
    assert await SteamInventoryClient(StubFetch(body=body)).get_inventory(STEAM_ID) == []


async def test_not_found_is_empty_list() -> None:
    fetch = StubFetch(status=404, body="null")
    assert await SteamInventoryClient(fetch).get_inventory(STEAM_ID) == []


async def test_forbidden_is_private_inventory() -> None:
    fetch = StubFetch(status=403, body="null")
    with pytest.raises(PrivateInventoryError):
        await SteamInventoryClient(fetch).get_inventory(STEAM_ID)


async def test_rate_limit_message_mentions_steam() -> None:
    fetch = StubFetch(status=429, body="null")
    with pytest.raises(RateLimitedError) as info:
        await SteamInventoryClient(fetch).get_inventory(STEAM_ID)
    assert info.value.message == INVENTORY_RATE_LIMITED_MESSAGE
    assert "Steam" in info.value.message


@pytest.mark.parametrize("status", [301, 302, 400, 401, 500, 502, 503])
async def test_other_statuses_are_api_errors(status: int) -> None:
    with pytest.raises(ApiError) as info:
        await SteamInventoryClient(StubFetch(status=status)).get_inventory(STEAM_ID)
    assert info.value.message == INVENTORY_ERROR_MESSAGE


@pytest.mark.parametrize("body", ["", "<html>Sorry!</html>", '{"assets": ', '{"success": 0}'])
async def test_undecodable_or_failed_bodies_are_api_errors(body: str) -> None:
    with pytest.raises(ApiError):
        await SteamInventoryClient(StubFetch(body=body)).get_inventory(STEAM_ID)


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientConnectionError("boom"),
        OSError("network down"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
)
async def test_fetch_errors_are_api_errors(error: BaseException) -> None:
    fetch = StubFetch()
    fetch.error = error
    with pytest.raises(ApiError):
        await SteamInventoryClient(fetch).get_inventory(STEAM_ID)


async def test_timeout_is_api_error() -> None:
    fetch = StubFetch()
    fetch.delay_s = 1.0
    with pytest.raises(ApiError):
        await SteamInventoryClient(fetch, timeout_s=0.01).get_inventory(STEAM_ID)


async def test_oversize_injected_body_is_api_error() -> None:
    body = '{"assets": [], "padding": "' + " " * MAX_INVENTORY_BYTES + '"}'
    with pytest.raises(ApiError):
        await SteamInventoryClient(StubFetch(body=body)).get_inventory(STEAM_ID)


@pytest.mark.parametrize(
    "steam_id",
    [
        "",
        "123",
        "7656119796028793",
        "765611979602879300",
        "76561197960287930/../../x",
        "76561197960287930?count=1",
        "７６５６１１９７９６０２８７９３０",
        f"https://steamcommunity.com/profiles/{STEAM_ID}",
        "gaben",
    ],
)
async def test_invalid_id_makes_no_request(steam_id: str) -> None:
    fetch = StubFetch()
    with pytest.raises(InvalidSteamIdError):
        await SteamInventoryClient(fetch).get_inventory(steam_id)
    assert fetch.calls == []


def test_inventory_url() -> None:
    assert inventory_url(STEAM_ID) == EXPECTED_URL


async def test_logs_never_include_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ApiError):
        await SteamInventoryClient(StubFetch(body="secret-body-text")).get_inventory(STEAM_ID)
    assert "secret-body-text" not in caplog.text


async def test_aclose_without_session_is_noop() -> None:
    client = SteamInventoryClient()
    await client.aclose()
    await client.aclose()


async def chunks_of(data: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


class FakeContent:
    def __init__(self, data: bytes, chunk_size: int) -> None:
        self.data = data
        self.chunk_size = chunk_size
        self.chunk_sizes: list[int] = []

    def iter_chunked(self, n: int) -> AsyncIterator[bytes]:
        self.chunk_sizes.append(n)
        return chunks_of(self.data, self.chunk_size)

    async def read(self, n: int = -1) -> bytes:
        raise AssertionError("bodies must be read with read_capped")


class FakeResponse:
    def __init__(self, data: bytes, chunk_size: int, status: int) -> None:
        self.status = status
        self.content = FakeContent(data, chunk_size)

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _fake_session(
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    seen: dict[str, Any],
    chunk_size: int = READ_CHUNK_BYTES,
    status: int = 200,
) -> list[FakeResponse]:
    responses: list[FakeResponse] = []

    class FakeSession:
        closed = False

        def __init__(self, timeout: aiohttp.ClientTimeout) -> None:
            seen["timeout"] = timeout
            seen["sessions"] = seen.get("sessions", 0) + 1

        def get(self, url: str, headers: dict[str, str], allow_redirects: bool) -> FakeResponse:
            seen["url"], seen["allow_redirects"] = url, allow_redirects
            responses.append(FakeResponse(data, chunk_size, status))
            return responses[-1]

        async def close(self) -> None:
            self.closed = True
            seen["closes"] = seen.get("closes", 0) + 1

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    return responses


async def test_default_fetch_creates_and_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    responses = _fake_session(monkeypatch, FIXTURE.encode(), seen, chunk_size=100)
    client = SteamInventoryClient()
    first = await client.get_inventory(STEAM_ID)
    assert await client.get_inventory(STEAM_ID) == first
    await client.aclose()
    await client.aclose()

    assert first
    assert seen["sessions"] == 1
    assert seen["closes"] == 1
    assert seen["timeout"].total == 15.0
    assert seen["url"] == EXPECTED_URL
    assert seen["allow_redirects"] is False
    assert all(r.content.chunk_sizes == [READ_CHUNK_BYTES] for r in responses)


async def test_default_fetch_oversize_body_is_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b'{"assets": [], "padding": "' + b" " * MAX_INVENTORY_BYTES + b'"}'
    _fake_session(monkeypatch, data, {}, chunk_size=1024 * 1024)
    client = SteamInventoryClient()
    with pytest.raises(ApiError):
        await client.get_inventory(STEAM_ID)
    await client.aclose()


async def test_default_fetch_body_at_cap_minus_one_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"null".ljust(MAX_INVENTORY_BYTES - 1)
    _fake_session(monkeypatch, data, {}, chunk_size=1024 * 1024)
    client = SteamInventoryClient()
    assert await client.get_inventory(STEAM_ID) == []
    await client.aclose()


async def test_default_fetch_invalid_utf8_is_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session(monkeypatch, b'{"assets": "\xff\xfe"}', {})
    client = SteamInventoryClient()
    with pytest.raises(ApiError):
        await client.get_inventory(STEAM_ID)
    await client.aclose()


async def test_default_fetch_private_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session(monkeypatch, b"null", {}, status=403)
    client = SteamInventoryClient()
    with pytest.raises(PrivateInventoryError):
        await client.get_inventory(STEAM_ID)
    await client.aclose()
