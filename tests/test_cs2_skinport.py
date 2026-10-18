import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from ninja_bot.cs2.client import FetchResponse
from ninja_bot.cs2.models import ApiError, RateLimitedError
from ninja_bot.cs2.prices import CURRENCIES
from ninja_bot.cs2.resolver import READ_CHUNK_BYTES
from ninja_bot.cs2.skinport import (
    SKINPORT_ERROR_MESSAGE,
    SKINPORT_MAX_BODY_BYTES,
    SKINPORT_RATE_LIMITED_MESSAGE,
    SkinportClient,
    parse_skinport,
    skinport_url,
    to_minor_units,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "skinport" / "items.json").read_text()
EUR = CURRENCIES["EUR"]
USD = CURRENCIES["USD"]
EXPECTED_URL = "https://api.skinport.com/v1/items?app_id=730&currency=EUR&tradable=0"
EXPECTED_PRICES = {
    "AK-47 | Redline (Field-Tested)": 2837,
    "Recoil Case": 29,
    "★ Karambit | Doppler (Factory New)": 109999,
}


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


def test_cap_is_32_mib() -> None:
    assert SKINPORT_MAX_BODY_BYTES == 32 * 1024 * 1024


def test_skinport_url_uses_currency_code() -> None:
    assert skinport_url(EUR) == EXPECTED_URL
    assert "currency=USD" in skinport_url(USD)


async def test_success_requests_url_with_headers_and_parses() -> None:
    fetch = StubFetch()
    prices = await SkinportClient(EUR, fetch).get_prices()
    [(url, headers)] = fetch.calls
    assert url == EXPECTED_URL
    assert headers["Accept-Encoding"] == "br"
    assert "ninja-discord-bot" in headers["User-Agent"]
    assert prices == EXPECTED_PRICES


def test_parse_fixture_omits_null_prices_and_keeps_stacks() -> None:
    payload = json.loads(FIXTURE, parse_float=Decimal)
    prices = parse_skinport(payload, EUR)
    assert prices == EXPECTED_PRICES
    assert "Sticker | Example Team (Holo) | Example Major 2024" not in prices


def test_parse_empty_list() -> None:
    assert parse_skinport([], EUR) == {}


def test_duplicate_name_keeps_lowest_price() -> None:
    payload = [
        {"market_hash_name": "X", "currency": "EUR", "min_price": Decimal("2.00")},
        {"market_hash_name": "X", "currency": "EUR", "min_price": Decimal("1.50")},
    ]
    assert parse_skinport(payload, EUR) == {"X": 150}


@pytest.mark.parametrize(
    ("value", "minor"),
    [
        (Decimal("0.29"), 29),
        (Decimal("1099.99"), 109999),
        (Decimal("0.1"), 10),
        (Decimal("2499"), 249900),
        (5, 500),
        ("12.34", 1234),
        (Decimal("0.005"), 1),
        (Decimal("0"), 0),
    ],
)
def test_to_minor_units(value: object, minor: int) -> None:
    assert to_minor_units(value) == minor


def test_no_float_drift_on_values_floats_get_wrong() -> None:
    # float(1.15) * 100 == 114.99999999999999; parsed as Decimal it stays exact.
    body = '[{"market_hash_name": "X", "currency": "EUR", "min_price": 1.15}]'
    assert parse_skinport(json.loads(body, parse_float=Decimal), EUR) == {"X": 115}
    assert int(1.15 * 100) == 114


@pytest.mark.parametrize("value", [1.15, True, "abc", "NaN", "Infinity", Decimal("-1"), [], {}])
def test_bad_prices_are_api_errors(value: object) -> None:
    with pytest.raises(ApiError):
        to_minor_units(value)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"items": []},
        [1],
        [{"currency": "EUR", "min_price": 1}],
        [{"market_hash_name": "", "currency": "EUR", "min_price": 1}],
        [{"market_hash_name": 3, "currency": "EUR", "min_price": 1}],
        [{"market_hash_name": "X", "currency": "USD", "min_price": 1}],
        [{"market_hash_name": "X", "currency": "EUR", "min_price": "free"}],
    ],
)
def test_malformed_payloads_are_api_errors(payload: object) -> None:
    with pytest.raises(ApiError) as info:
        parse_skinport(payload, EUR)
    assert info.value.message == SKINPORT_ERROR_MESSAGE


async def test_rate_limit_is_rate_limited_error() -> None:
    with pytest.raises(RateLimitedError) as info:
        await SkinportClient(EUR, StubFetch(status=429, body="")).get_prices()
    assert info.value.message == SKINPORT_RATE_LIMITED_MESSAGE


@pytest.mark.parametrize("status", [301, 400, 403, 404, 500, 502, 503])
async def test_other_statuses_are_api_errors(status: int) -> None:
    with pytest.raises(ApiError) as info:
        await SkinportClient(EUR, StubFetch(status=status)).get_prices()
    assert info.value.message == SKINPORT_ERROR_MESSAGE


@pytest.mark.parametrize("body", ["", "<html>Cloudflare</html>", "[{", "null"])
async def test_undecodable_bodies_are_api_errors(body: str) -> None:
    with pytest.raises(ApiError):
        await SkinportClient(EUR, StubFetch(body=body)).get_prices()


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
        await SkinportClient(EUR, fetch).get_prices()


async def test_timeout_is_api_error() -> None:
    fetch = StubFetch()
    fetch.delay_s = 1.0
    with pytest.raises(ApiError):
        await SkinportClient(EUR, fetch, timeout_s=0.01).get_prices()


async def test_oversize_injected_body_is_api_error() -> None:
    body = '[{"padding": "' + " " * SKINPORT_MAX_BODY_BYTES + '"}]'
    with pytest.raises(ApiError):
        await SkinportClient(EUR, StubFetch(body=body)).get_prices()


async def test_logs_never_include_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ApiError):
        await SkinportClient(EUR, StubFetch(body="secret-body-text")).get_prices()
    assert "secret-body-text" not in caplog.text


async def test_aclose_without_session_is_noop() -> None:
    client = SkinportClient(EUR)
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
            seen["url"], seen["headers"] = url, headers
            seen["allow_redirects"] = allow_redirects
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
    client = SkinportClient(EUR)
    first = await client.get_prices()
    assert await client.get_prices() == first
    await client.aclose()
    await client.aclose()

    assert first == EXPECTED_PRICES
    assert seen["sessions"] == 1
    assert seen["closes"] == 1
    assert seen["url"] == EXPECTED_URL
    assert seen["headers"]["Accept-Encoding"] == "br"
    assert seen["allow_redirects"] is False
    assert all(r.content.chunk_sizes == [READ_CHUNK_BYTES] for r in responses)


async def test_default_fetch_oversize_body_is_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b'[{"padding": "' + b" " * SKINPORT_MAX_BODY_BYTES + b'"}]'
    _fake_session(monkeypatch, data, {}, chunk_size=4 * 1024 * 1024)
    client = SkinportClient(EUR)
    with pytest.raises(ApiError):
        await client.get_prices()
    await client.aclose()


async def test_default_fetch_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session(monkeypatch, b"", {}, status=429)
    client = SkinportClient(EUR)
    with pytest.raises(RateLimitedError):
        await client.get_prices()
    await client.aclose()


def test_brotli_is_installed_for_aiohttp() -> None:
    import brotli  # noqa: F401
