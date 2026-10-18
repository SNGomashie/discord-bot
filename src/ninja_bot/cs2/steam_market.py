"""Steam Market price client: crawls the CS2 market listing pages for wallet-value prices."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TypeGuard

import aiohttp

from ninja_bot.cs2.client import FetchFn, FetchResponse
from ninja_bot.cs2.models import ApiError, Cs2Error, RateLimitedError
from ninja_bot.cs2.prices import Currency
from ninja_bot.cs2.resolver import READ_CHUNK_BYTES, read_capped

log = logging.getLogger(__name__)

MARKET_SEARCH_URL = "https://steamcommunity.com/market/search/render/"
MARKET_HEADERS: Mapping[str, str] = {
    "Accept": "application/json",
    "User-Agent": "ninja-discord-bot (private Discord bot; CS2 skin prices)",
}
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 15.0
# A page of 100 results with asset descriptions is a few hundred KiB; a body at the
# cap is refused, never parsed cut off.
MARKET_MAX_BODY_BYTES = 2 * 1024 * 1024

# Steam allows only a handful of market requests per minute per IP.
MIN_PAGE_INTERVAL_SECONDS = 5.0
# About 300 pages cover every CS2 item; the cap stops a runaway crawl.
MAX_PAGES = 500
# A 429 page is retried after `RATE_LIMIT_BACKOFF_SECONDS` x attempt, at most this often.
MAX_RETRIES = 3
RATE_LIMIT_BACKOFF_SECONDS = 60.0

MARKET_ERROR_MESSAGE = "The Steam Market couldn't be reached or sent something unexpected."
MARKET_RATE_LIMITED_MESSAGE = "The Steam Market is rate limiting price requests right now."

SleepFn = Callable[[float], Awaitable[None]]
PageFn = Callable[["MarketPage"], Awaitable[None]]


class _OversizeBodyError(Exception):
    """The default fetch stopped reading at `MARKET_MAX_BODY_BYTES`."""


@dataclass(frozen=True)
class MarketPage:
    """One search page: Steam's total listing count, priced names and the result count."""

    total_count: int
    prices: dict[str, int]
    # Results on the page, priced or not (advances `start`).
    entries: int


@dataclass(frozen=True)
class CrawlResult:
    """Prices gathered by `crawl`; `complete` is False when it stopped early on an error."""

    prices: dict[str, int]
    pages: int
    complete: bool


def market_url(start: int, currency: Currency) -> str:
    """The search URL for CS2 (app 730) results `start..start+99`, priciest first."""
    if start < 0:
        raise ValueError("start must not be negative")
    return (
        f"{MARKET_SEARCH_URL}?appid=730&norender=1&sort_column=price&sort_order=desc"
        f"&start={start}&count={PAGE_SIZE}&currency={currency.steam_id}"
    )


def parse_market_page(payload: object) -> MarketPage:
    """`hash_name -> sell_price` (minor units); missing or zero prices are omitted.

    `success: false` or a malformed body raises ApiError. A name listed twice keeps its
    lowest price.
    """
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ApiError(MARKET_ERROR_MESSAGE)
    total = payload.get("total_count")
    results = payload.get("results")
    if not _is_int(total) or total < 0 or not isinstance(results, list):
        raise ApiError(MARKET_ERROR_MESSAGE)
    prices: dict[str, int] = {}
    for entry in results:
        if not isinstance(entry, dict):
            raise ApiError(MARKET_ERROR_MESSAGE)
        name = entry.get("hash_name")
        if not isinstance(name, str) or not name:
            raise ApiError(MARKET_ERROR_MESSAGE)
        price = entry.get("sell_price")
        if price is None:
            continue
        if not _is_int(price):
            raise ApiError(MARKET_ERROR_MESSAGE)
        if price <= 0:
            continue
        if name not in prices or price < prices[name]:
            prices[name] = price
    return MarketPage(total_count=total, prices=prices, entries=len(results))


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


class SteamMarketClient:
    """Fetches Steam Market prices page by page, gently enough for Steam's per-IP limit."""

    def __init__(
        self,
        currency: Currency,
        fetch: FetchFn | None = None,
        sleep: SleepFn = asyncio.sleep,
        timeout_s: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._currency = currency
        self._fetch = fetch or self._aiohttp_fetch
        self._sleep = sleep
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def fetch_page(self, start: int) -> MarketPage:
        url = market_url(start, self._currency)
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._fetch(url, MARKET_HEADERS)
        except TimeoutError:
            log.warning("Steam Market request timed out")
            raise ApiError(MARKET_ERROR_MESSAGE) from None
        except (aiohttp.ClientError, OSError, UnicodeDecodeError, _OversizeBodyError) as exc:
            log.warning("Steam Market request failed (%s)", type(exc).__name__)
            raise ApiError(MARKET_ERROR_MESSAGE) from None
        return _page_from_response(response)

    async def crawl(self, on_page: PageFn | None = None) -> CrawlResult:
        """Walk the pages until `total_count` (or `MAX_PAGES`), calling `on_page` for each.

        Pages are requested `MIN_PAGE_INTERVAL_SECONDS` apart. A 429 is retried after a
        backoff up to `MAX_RETRIES` times; giving up, any other error or an empty page
        before the end returns what was gathered with `complete=False` (never raises
        Cs2Error). Hitting `MAX_PAGES` counts as complete: a retry would stop there too.
        """
        prices: dict[str, int] = {}
        pages = 0
        start = 0
        while pages < MAX_PAGES:
            if pages:
                await self._sleep(MIN_PAGE_INTERVAL_SECONDS)
            try:
                page = await self._fetch_with_retries(start)
            except Cs2Error as exc:
                log.warning(
                    "Steam Market crawl stopped after %d pages (%s)", pages, type(exc).__name__
                )
                return CrawlResult(prices, pages, complete=False)
            pages += 1
            prices.update(page.prices)
            if on_page is not None:
                await on_page(page)
            start += page.entries
            if start >= page.total_count:
                return CrawlResult(prices, pages, complete=True)
            if page.entries == 0:
                log.warning("Steam Market sent an empty page at %d of %d", start, page.total_count)
                return CrawlResult(prices, pages, complete=False)
        log.warning("Steam Market crawl stopped at the %d page cap", MAX_PAGES)
        return CrawlResult(prices, pages, complete=True)

    async def aclose(self) -> None:
        """Close the lazily created aiohttp session; safe to call more than once."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _fetch_with_retries(self, start: int) -> MarketPage:
        attempt = 0
        while True:
            try:
                return await self.fetch_page(start)
            except RateLimitedError:
                if attempt >= MAX_RETRIES:
                    raise
                attempt += 1
                await self._sleep(RATE_LIMIT_BACKOFF_SECONDS * attempt)

    async def _aiohttp_fetch(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_s)
            )
        async with self._session.get(url, headers=dict(headers), allow_redirects=False) as response:
            data, complete = await read_capped(
                response.content.iter_chunked(READ_CHUNK_BYTES), MARKET_MAX_BODY_BYTES
            )
            if not complete:
                raise _OversizeBodyError()
            return FetchResponse(response.status, data.decode("utf-8"))


def _page_from_response(response: FetchResponse) -> MarketPage:
    status = response.status
    if status == 429:
        log.warning("Steam Market request was rate limited")
        raise RateLimitedError(MARKET_RATE_LIMITED_MESSAGE)
    if not 200 <= status < 300:
        log.warning("Steam Market request returned HTTP %d", status)
        raise ApiError(MARKET_ERROR_MESSAGE)
    # Injected fetches may return more than the default one reads; apply the same cap.
    if len(response.body.encode("utf-8", errors="replace")) >= MARKET_MAX_BODY_BYTES:
        log.warning("Steam Market body is larger than %d bytes", MARKET_MAX_BODY_BYTES)
        raise ApiError(MARKET_ERROR_MESSAGE)
    try:
        payload = json.loads(response.body)
    except ValueError:
        log.warning("Steam Market body is not JSON")
        raise ApiError(MARKET_ERROR_MESSAGE) from None
    return parse_market_page(payload)
