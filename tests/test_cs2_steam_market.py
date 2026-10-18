import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from ninja_bot.cs2 import steam_market
from ninja_bot.cs2.client import FetchResponse
from ninja_bot.cs2.models import ApiError, RateLimitedError
from ninja_bot.cs2.prices import CURRENCIES
from ninja_bot.cs2.resolver import READ_CHUNK_BYTES
from ninja_bot.cs2.steam_market import (
    MARKET_MAX_BODY_BYTES,
    MARKET_RATE_LIMITED_MESSAGE,
    MAX_PAGES,
    MAX_RETRIES,
    MIN_PAGE_INTERVAL_SECONDS,
    RATE_LIMIT_BACKOFF_SECONDS,
    MarketPage,
    SteamMarketClient,
    market_url,
    parse_market_page,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "steam" / "market_search.json").read_text()
EUR = CURRENCIES["EUR"]
USD = CURRENCIES["USD"]
EXPECTED_URL = (
    "https://steamcommunity.com/market/search/render/?appid=730&norender=1"
    "&sort_column=price&sort_order=desc&start=0&count=100&currency=3"
)
EXPECTED_PRICES = {
    "★ Karambit | Doppler (Factory New)": 123456,
    "AK-47 | Redline (Field-Tested)": 3012,
    "Recoil Case": 31,
}


def page_body(start: int, names: list[str], total: int, success: bool = True) -> str:
    results = [{"hash_name": name, "sell_price": 100 + i} for i, name in enumerate(names)]
    return json.dumps(
        {"success": success, "start": start, "total_count": total, "results": results}
    )


class StubFetch:
    """Answers each call with the next queued (status, body); the last one repeats."""

    def __init__(self, *responses: tuple[int, str]) -> None:
        self.responses = list(responses) or [(200, FIXTURE)]
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.error: BaseException | None = None
        self.delay_s = 0.0

    async def __call__(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        self.calls.append((url, dict(headers)))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        status, body = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        return FetchResponse(status, body)

    def starts(self) -> list[int]:
        return [int(url.split("&start=")[1].split("&")[0]) for url, _ in self.calls]


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client(fetch: StubFetch) -> tuple[SteamMarketClient, FakeSleep]:
    sleep = FakeSleep()
    return SteamMarketClient(EUR, fetch, sleep=sleep), sleep


def test_constants() -> None:
    assert MIN_PAGE_INTERVAL_SECONDS == 5
    assert MAX_PAGES == 500


def test_market_url_uses_start_and_steam_currency_id() -> None:
    assert market_url(0, EUR) == EXPECTED_URL
    assert "&start=200&" in market_url(200, USD)
    assert market_url(0, USD).endswith("&currency=1")
    with pytest.raises(ValueError):
        market_url(-1, EUR)


def test_parse_fixture_omits_missing_and_zero_prices() -> None:
    page = parse_market_page(json.loads(FIXTURE))
    assert page == MarketPage(total_count=5, prices=EXPECTED_PRICES, entries=5)


def test_parse_empty_results() -> None:
    page = parse_market_page({"success": True, "total_count": 0, "results": []})
    assert page == MarketPage(total_count=0, prices={}, entries=0)


def test_duplicate_name_keeps_lowest_price() -> None:
    payload = {
        "success": True,
        "total_count": 2,
        "results": [{"hash_name": "A", "sell_price": 50}, {"hash_name": "A", "sell_price": 40}],
    }
    assert parse_market_page(payload).prices == {"A": 40}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"success": False, "total_count": 1, "results": []},
        {"success": 1, "total_count": 1, "results": []},
        {"success": True, "results": []},
        {"success": True, "total_count": "5", "results": []},
        {"success": True, "total_count": True, "results": []},
        {"success": True, "total_count": -1, "results": []},
        {"success": True, "total_count": 1, "results": None},
        {"success": True, "total_count": 1, "results": ["x"]},
        {"success": True, "total_count": 1, "results": [{"sell_price": 5}]},
        {"success": True, "total_count": 1, "results": [{"hash_name": "", "sell_price": 5}]},
        {"success": True, "total_count": 1, "results": [{"hash_name": "A", "sell_price": "5"}]},
        {"success": True, "total_count": 1, "results": [{"hash_name": "A", "sell_price": 1.5}]},
        {"success": True, "total_count": 1, "results": [{"hash_name": "A", "sell_price": True}]},
    ],
)
def test_malformed_payloads_are_api_errors(payload: object) -> None:
    with pytest.raises(ApiError):
        parse_market_page(payload)


async def test_fetch_page_requests_url_with_headers_and_parses() -> None:
    fetch = StubFetch()
    client, _ = make_client(fetch)
    page = await client.fetch_page(0)
    [(url, headers)] = fetch.calls
    assert url == EXPECTED_URL
    assert "ninja-discord-bot" in headers["User-Agent"]
    assert page.prices == EXPECTED_PRICES


async def test_fetch_page_rate_limit_is_rate_limited_error() -> None:
    client, _ = make_client(StubFetch((429, "")))
    with pytest.raises(RateLimitedError, match=MARKET_RATE_LIMITED_MESSAGE):
        await client.fetch_page(0)


@pytest.mark.parametrize("status", [302, 400, 403, 500, 502])
async def test_fetch_page_other_statuses_are_api_errors(status: int) -> None:
    client, _ = make_client(StubFetch((status, FIXTURE)))
    with pytest.raises(ApiError):
        await client.fetch_page(0)


@pytest.mark.parametrize("body", ["", "null", "<html></html>", '{"success": false}'])
async def test_fetch_page_bad_bodies_are_api_errors(body: str) -> None:
    client, _ = make_client(StubFetch((200, body)))
    with pytest.raises(ApiError):
        await client.fetch_page(0)


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientConnectionError("boom"),
        OSError("boom"),
        UnicodeDecodeError("utf-8", b"", 0, 1, "x"),
    ],
)
async def test_fetch_page_errors_are_api_errors(error: BaseException) -> None:
    fetch = StubFetch()
    fetch.error = error
    client, _ = make_client(fetch)
    with pytest.raises(ApiError):
        await client.fetch_page(0)


async def test_fetch_page_timeout_is_api_error() -> None:
    fetch = StubFetch()
    fetch.delay_s = 1.0
    client = SteamMarketClient(EUR, fetch, timeout_s=0.01)
    with pytest.raises(ApiError):
        await client.fetch_page(0)


async def test_oversize_injected_body_is_api_error() -> None:
    client, _ = make_client(StubFetch((200, " " * MARKET_MAX_BODY_BYTES)))
    with pytest.raises(ApiError):
        await client.fetch_page(0)


async def test_crawl_walks_pages_until_total_count() -> None:
    fetch = StubFetch(
        (200, page_body(0, [f"a{i}" for i in range(100)], 250)),
        (200, page_body(100, [f"b{i}" for i in range(100)], 250)),
        (200, page_body(200, [f"c{i}" for i in range(50)], 250)),
    )
    client, sleep = make_client(fetch)
    seen: list[MarketPage] = []

    async def on_page(page: MarketPage) -> None:
        seen.append(page)

    result = await client.crawl(on_page)
    assert fetch.starts() == [0, 100, 200]
    assert sleep.calls == [MIN_PAGE_INTERVAL_SECONDS, MIN_PAGE_INTERVAL_SECONDS]
    assert result.complete is True
    assert result.pages == 3
    assert len(result.prices) == 250
    assert [page.entries for page in seen] == [100, 100, 50]


async def test_crawl_advances_by_entries_received() -> None:
    fetch = StubFetch(
        (200, page_body(0, [f"a{i}" for i in range(10)], 15)),
        (200, page_body(10, [f"b{i}" for i in range(5)], 15)),
    )
    client, _ = make_client(fetch)
    result = await client.crawl()
    assert fetch.starts() == [0, 10]
    assert result.complete is True


async def test_crawl_empty_market_is_complete() -> None:
    fetch = StubFetch((200, page_body(0, [], 0)))
    client, sleep = make_client(fetch)
    result = await client.crawl()
    assert result.complete is True
    assert result.prices == {}
    assert sleep.calls == []


async def test_crawl_empty_page_before_the_end_is_incomplete() -> None:
    fetch = StubFetch((200, page_body(0, ["a"], 300)), (200, page_body(1, [], 300)))
    client, _ = make_client(fetch)
    result = await client.crawl()
    assert result.complete is False
    assert result.prices == {"a": 100}
    assert result.pages == 2


async def test_crawl_retries_rate_limited_page_after_backoff() -> None:
    fetch = StubFetch(
        (200, page_body(0, ["a"], 2)),
        (429, ""),
        (429, ""),
        (200, page_body(1, ["b"], 2)),
    )
    client, sleep = make_client(fetch)
    result = await client.crawl()
    assert fetch.starts() == [0, 1, 1, 1]
    assert sleep.calls == [
        MIN_PAGE_INTERVAL_SECONDS,
        RATE_LIMIT_BACKOFF_SECONDS,
        RATE_LIMIT_BACKOFF_SECONDS * 2,
    ]
    assert result.complete is True
    assert result.prices == {"a": 100, "b": 100}


async def test_crawl_gives_up_after_max_retries_and_keeps_gathered_prices() -> None:
    fetch = StubFetch((200, page_body(0, ["a", "b"], 10)), (429, ""))
    client, sleep = make_client(fetch)
    seen: list[MarketPage] = []

    async def on_page(page: MarketPage) -> None:
        seen.append(page)

    result = await client.crawl(on_page)
    assert len(fetch.calls) == 1 + 1 + MAX_RETRIES
    assert sleep.calls[1:] == [RATE_LIMIT_BACKOFF_SECONDS * n for n in range(1, MAX_RETRIES + 1)]
    assert result.complete is False
    assert result.pages == 1
    assert result.prices == {"a": 100, "b": 101}
    assert len(seen) == 1


async def test_crawl_other_error_is_incomplete_without_retry() -> None:
    fetch = StubFetch((200, page_body(0, ["a"], 10)), (500, ""))
    client, sleep = make_client(fetch)
    result = await client.crawl()
    assert len(fetch.calls) == 2
    assert sleep.calls == [MIN_PAGE_INTERVAL_SECONDS]
    assert result.complete is False
    assert result.prices == {"a": 100}


async def test_crawl_first_page_failure_is_incomplete_and_empty() -> None:
    client, _ = make_client(StubFetch((200, "null")))
    result = await client.crawl()
    assert result.complete is False
    assert result.pages == 0
    assert result.prices == {}


async def test_crawl_stops_at_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_market, "MAX_PAGES", 3)
    fetch = StubFetch((200, page_body(0, ["a"], 1000)))
    client, _ = make_client(fetch)
    result = await client.crawl()
    assert len(fetch.calls) == 3
    assert result.pages == 3
    assert result.complete is True


async def test_logs_never_include_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(StubFetch((500, "secret-body")))
    with pytest.raises(ApiError):
        await client.fetch_page(0)
    assert "secret-body" not in caplog.text


async def test_aclose_without_session_is_noop() -> None:
    client, _ = make_client(StubFetch())
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
    client = SteamMarketClient(EUR)
    first = await client.fetch_page(0)
    assert await client.fetch_page(0) == first
    await client.aclose()
    await client.aclose()

    assert first.prices == EXPECTED_PRICES
    assert seen["sessions"] == 1
    assert seen["closes"] == 1
    assert seen["url"] == EXPECTED_URL
    assert seen["allow_redirects"] is False
    assert all(r.content.chunk_sizes == [READ_CHUNK_BYTES] for r in responses)


async def test_default_fetch_oversize_body_is_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b'{"padding": "' + b" " * MARKET_MAX_BODY_BYTES + b'"}'
    _fake_session(monkeypatch, data, {}, chunk_size=1024 * 1024)
    client = SteamMarketClient(EUR)
    with pytest.raises(ApiError):
        await client.fetch_page(0)
    await client.aclose()


async def test_default_fetch_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session(monkeypatch, b"", {}, status=429)
    client = SteamMarketClient(EUR)
    with pytest.raises(RateLimitedError):
        await client.fetch_page(0)
    await client.aclose()
