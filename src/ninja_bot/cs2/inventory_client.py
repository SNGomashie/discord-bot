"""Steam inventory client: fetches a player's CS2 inventory from steamcommunity.com."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping

import aiohttp

from ninja_bot.cs2.client import FetchFn, FetchResponse
from ninja_bot.cs2.inventory import INVENTORY_ERROR_MESSAGE, InventoryItem, parse_inventory
from ninja_bot.cs2.models import (
    ApiError,
    InvalidSteamIdError,
    PrivateInventoryError,
    RateLimitedError,
)
from ninja_bot.cs2.resolver import READ_CHUNK_BYTES, read_capped
from ninja_bot.cs2.steam import is_steam_id

log = logging.getLogger(__name__)

INVENTORY_URL_TEMPLATE = "https://steamcommunity.com/inventory/{steam64_id}/730/2"
INVENTORY_QUERY = "l=english&count=2000"
REQUEST_TIMEOUT_SECONDS = 15.0
# A full 2000-item inventory is a few MiB of JSON; a larger body is refused, never parsed
# cut off. Much larger than the resolver's `MAX_BODY_BYTES` (profile XML).
MAX_INVENTORY_BYTES = 8 * 1024 * 1024

INVENTORY_RATE_LIMITED_MESSAGE = (
    "Steam is rate limiting inventory requests right now. Try again in a few minutes."
)


class _OversizeBodyError(Exception):
    """The default fetch stopped reading at `MAX_INVENTORY_BYTES`."""


def inventory_url(steam64_id: str) -> str:
    """The inventory URL for a SteamID64; raises InvalidSteamIdError for anything else."""
    if not is_steam_id(steam64_id):
        raise InvalidSteamIdError()
    return INVENTORY_URL_TEMPLATE.format(steam64_id=steam64_id) + "?" + INVENTORY_QUERY


class SteamInventoryClient:
    """Fetches CS2 inventories (appid 730, context 2) from the public Steam endpoint."""

    def __init__(
        self, fetch: FetchFn | None = None, timeout_s: float = REQUEST_TIMEOUT_SECONDS
    ) -> None:
        self._fetch = fetch or self._aiohttp_fetch
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def get_inventory(self, steam64_id: str) -> list[InventoryItem]:
        url = inventory_url(steam64_id)
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._fetch(url, {"Accept": "application/json"})
        except TimeoutError:
            log.warning("Steam inventory request timed out")
            raise ApiError(INVENTORY_ERROR_MESSAGE) from None
        except (aiohttp.ClientError, OSError, UnicodeDecodeError, _OversizeBodyError) as exc:
            log.warning("Steam inventory request failed (%s)", type(exc).__name__)
            raise ApiError(INVENTORY_ERROR_MESSAGE) from None
        return _items_from_response(response)

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
                response.content.iter_chunked(READ_CHUNK_BYTES), MAX_INVENTORY_BYTES
            )
            if not complete:
                raise _OversizeBodyError()
            return FetchResponse(response.status, data.decode("utf-8"))


def _items_from_response(response: FetchResponse) -> list[InventoryItem]:
    status = response.status
    if status == 403:
        log.info("Steam inventory is private")
        raise PrivateInventoryError()
    if status == 404:
        log.info("Steam inventory not found (HTTP 404); treated as empty")
        return []
    if status == 429:
        log.warning("Steam inventory request was rate limited")
        raise RateLimitedError(INVENTORY_RATE_LIMITED_MESSAGE)
    if not 200 <= status < 300:
        log.warning("Steam inventory request returned HTTP %d", status)
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    # Injected fetches may return more than the default one reads; apply the same cap.
    if len(response.body.encode("utf-8", errors="replace")) >= MAX_INVENTORY_BYTES:
        log.warning("Steam inventory body is larger than %d bytes", MAX_INVENTORY_BYTES)
        raise ApiError(INVENTORY_ERROR_MESSAGE)
    try:
        payload = json.loads(response.body)
    except ValueError:
        log.warning("Steam inventory body is not JSON")
        raise ApiError(INVENTORY_ERROR_MESSAGE) from None
    return parse_inventory(payload)
