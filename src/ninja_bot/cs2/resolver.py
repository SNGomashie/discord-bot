from __future__ import annotations

import asyncio
import codecs
import logging
import re
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote

import aiohttp

from ninja_bot.cs2.client import FetchFn, FetchResponse
from ninja_bot.cs2.models import (
    InvalidSteamIdError,
    RateLimitedError,
    SteamLookupError,
    VanityNotFoundError,
)
from ninja_bot.cs2.steam import is_vanity_name

log = logging.getLogger(__name__)

STEAM_COMMUNITY_SCHEME = "https"
STEAM_COMMUNITY_HOST = "steamcommunity.com"
VANITY_PATH_PREFIX = "/id/"
REQUEST_TIMEOUT_SECONDS = 10.0
# Profile XML is a few KiB; anything past this is ignored (and never searched).
MAX_BODY_BYTES = 64 * 1024

# Regex only, never an XML parser: the endpoint is unofficial and the body untrusted.
_STEAM_ID64_RE = re.compile(r"<steamID64>(7656119\d{10})</steamID64>", re.ASCII)
_ERROR_RE = re.compile(r"<error\b", re.ASCII)

STEAM_RATE_LIMITED_MESSAGE = (
    "Steam is rate limiting name lookups right now. "
    "Try again later, or use your 17-digit SteamID64 instead."
)


class SteamResolver(Protocol):
    async def resolve_vanity(self, name: str) -> str: ...

    async def aclose(self) -> None: ...


def vanity_url(name: str) -> str:
    """The profile XML URL for a custom URL name; raises InvalidSteamIdError for bad names."""
    if not is_vanity_name(name):
        raise InvalidSteamIdError()
    return (
        f"{STEAM_COMMUNITY_SCHEME}://{STEAM_COMMUNITY_HOST}"
        f"{VANITY_PATH_PREFIX}{quote(name, safe='')}/?xml=1"
    )


class SteamCommunityResolver:
    """Resolves Steam custom URL names via the public profile XML; the only module
    that talks to steamcommunity.com."""

    def __init__(
        self, fetch: FetchFn | None = None, timeout_s: float = REQUEST_TIMEOUT_SECONDS
    ) -> None:
        self._fetch = fetch or self._aiohttp_fetch
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def resolve_vanity(self, name: str) -> str:
        url = vanity_url(name)
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._fetch(url, {"Accept": "text/xml"})
        except TimeoutError:
            log.warning("Steam vanity lookup timed out")
            raise SteamLookupError() from None
        except (aiohttp.ClientError, OSError, UnicodeDecodeError) as exc:
            log.warning("Steam vanity lookup failed (%s)", type(exc).__name__)
            raise SteamLookupError() from None
        return _steam_id_from_response(response)

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
            data = await response.content.read(MAX_BODY_BYTES)
            # A capped read may end inside a multi-byte character; only a complete body
            # must decode completely. Invalid UTF-8 still raises UnicodeDecodeError.
            complete = len(data) < MAX_BODY_BYTES
            body = codecs.getincrementaldecoder("utf-8")().decode(data, final=complete)
            return FetchResponse(response.status, body)


def _steam_id_from_response(response: FetchResponse) -> str:
    status = response.status
    if status == 404:
        log.warning("Steam vanity lookup returned HTTP 404")
        raise VanityNotFoundError()
    if status == 429:
        log.warning("Steam vanity lookup was rate limited")
        raise RateLimitedError(STEAM_RATE_LIMITED_MESSAGE)
    if not 200 <= status < 300:
        log.warning("Steam vanity lookup returned HTTP %d", status)
        raise SteamLookupError()
    # Injected fetches may return more than the default one reads; apply the same cap.
    body = response.body.encode("utf-8", errors="replace")[:MAX_BODY_BYTES]
    text = body.decode("utf-8", errors="ignore")
    if match := _STEAM_ID64_RE.search(text):
        return match.group(1)
    if _ERROR_RE.search(text):
        log.info("Steam vanity lookup: no profile with that custom URL")
    else:
        log.warning("Steam vanity lookup: response had no SteamID64")
    raise VanityNotFoundError()
