"""Skinport price client: every CS2 item's lowest listing price in one bulk call."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import aiohttp

from ninja_bot.cs2.client import FetchFn, FetchResponse
from ninja_bot.cs2.models import ApiError, RateLimitedError
from ninja_bot.cs2.prices import Currency
from ninja_bot.cs2.resolver import READ_CHUNK_BYTES, read_capped

log = logging.getLogger(__name__)

SKINPORT_ITEMS_URL = "https://api.skinport.com/v1/items"
# Skinport serves this endpoint Brotli-compressed only; aiohttp decodes `br` via the
# Brotli package (a runtime dependency).
SKINPORT_HEADERS: Mapping[str, str] = {
    "Accept": "application/json",
    "Accept-Encoding": "br",
    "User-Agent": "ninja-discord-bot (private Discord bot; CS2 skin prices)",
}
REQUEST_TIMEOUT_SECONDS = 60.0
# The decoded body is about 10 MB; a body at the cap is refused, never parsed cut off.
SKINPORT_MAX_BODY_BYTES = 32 * 1024 * 1024
MINOR_UNITS_PER_MAJOR = Decimal(100)

SKINPORT_ERROR_MESSAGE = "Skinport couldn't be reached or sent something unexpected."
SKINPORT_RATE_LIMITED_MESSAGE = "Skinport is rate limiting price requests right now."


class _OversizeBodyError(Exception):
    """The default fetch stopped reading at `SKINPORT_MAX_BODY_BYTES`."""


def skinport_url(currency: Currency) -> str:
    """The bulk items URL for CS2 (app 730) in `currency`, including untradable listings."""
    return f"{SKINPORT_ITEMS_URL}?app_id=730&currency={currency.skinport_code}&tradable=0"


def to_minor_units(value: object) -> int:
    """A price in major units (`12.34`, as Decimal, int or str) -> minor units (1234).

    Floats are refused: bodies are decoded with `parse_float=Decimal`, so a float here
    means precision was already lost.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
        raise ApiError(SKINPORT_ERROR_MESSAGE)
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ApiError(SKINPORT_ERROR_MESSAGE) from None
    if not amount.is_finite() or amount < 0:
        raise ApiError(SKINPORT_ERROR_MESSAGE)
    return int((amount * MINOR_UNITS_PER_MAJOR).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_skinport(payload: object, currency: Currency) -> dict[str, int]:
    """`market_hash_name -> min_price` in minor units; null prices are omitted.

    Malformed payloads, or items priced in another currency, raise ApiError. A name
    listed twice keeps its lowest price.
    """
    if not isinstance(payload, list):
        raise ApiError(SKINPORT_ERROR_MESSAGE)
    prices: dict[str, int] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            raise ApiError(SKINPORT_ERROR_MESSAGE)
        name = entry.get("market_hash_name")
        if not isinstance(name, str) or not name:
            raise ApiError(SKINPORT_ERROR_MESSAGE)
        item_currency = entry.get("currency", currency.skinport_code)
        if item_currency != currency.skinport_code:
            log.warning("Skinport sent prices in an unexpected currency")
            raise ApiError(SKINPORT_ERROR_MESSAGE)
        raw = entry.get("min_price")
        if raw is None:
            continue
        minor = to_minor_units(raw)
        if name not in prices or minor < prices[name]:
            prices[name] = minor
    return prices


class SkinportClient:
    """Fetches Skinport's lowest listing prices for all CS2 items in one request."""

    def __init__(
        self,
        currency: Currency,
        fetch: FetchFn | None = None,
        timeout_s: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._currency = currency
        self._fetch = fetch or self._aiohttp_fetch
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def get_prices(self) -> dict[str, int]:
        url = skinport_url(self._currency)
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._fetch(url, SKINPORT_HEADERS)
        except TimeoutError:
            log.warning("Skinport request timed out")
            raise ApiError(SKINPORT_ERROR_MESSAGE) from None
        except (aiohttp.ClientError, OSError, UnicodeDecodeError, _OversizeBodyError) as exc:
            log.warning("Skinport request failed (%s)", type(exc).__name__)
            raise ApiError(SKINPORT_ERROR_MESSAGE) from None
        return self._prices_from_response(response)

    async def aclose(self) -> None:
        """Close the lazily created aiohttp session; safe to call more than once."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _aiohttp_fetch(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_s)
            )
        async with self._session.get(url, headers=dict(headers), allow_redirects=False) as response:
            data, complete = await read_capped(
                response.content.iter_chunked(READ_CHUNK_BYTES), SKINPORT_MAX_BODY_BYTES
            )
            if not complete:
                raise _OversizeBodyError()
            return FetchResponse(response.status, data.decode("utf-8"))

    def _prices_from_response(self, response: FetchResponse) -> dict[str, int]:
        status = response.status
        if status == 429:
            log.warning("Skinport request was rate limited")
            raise RateLimitedError(SKINPORT_RATE_LIMITED_MESSAGE)
        if not 200 <= status < 300:
            log.warning("Skinport request returned HTTP %d", status)
            raise ApiError(SKINPORT_ERROR_MESSAGE)
        # Injected fetches may return more than the default one reads; apply the same cap.
        if len(response.body.encode("utf-8", errors="replace")) >= SKINPORT_MAX_BODY_BYTES:
            log.warning("Skinport body is larger than %d bytes", SKINPORT_MAX_BODY_BYTES)
            raise ApiError(SKINPORT_ERROR_MESSAGE)
        try:
            payload = json.loads(response.body, parse_float=Decimal)
        except ValueError:
            log.warning("Skinport body is not JSON")
            raise ApiError(SKINPORT_ERROR_MESSAGE) from None
        return parse_skinport(payload, self._currency)
