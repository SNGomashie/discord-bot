from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode

import aiohttp

from ninja_bot.cs2.models import (
    ApiError,
    Cs2Error,
    Match,
    MatchNotFoundError,
    PlayerNotFoundError,
    PrivateProfileError,
    Profile,
    RateLimitedError,
    newest_first,
    parse_match,
    parse_profile,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api-public.cs-prod.leetify.com"
REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: str


# One HTTP GET (url, headers) -> response; injectable so tests never touch the network.
# It may raise aiohttp.ClientError, OSError, UnicodeDecodeError (undecodable body) or
# TimeoutError; `LeetifyClient` turns all of them into ApiError.
FetchFn = Callable[[str, Mapping[str, str]], Awaitable[FetchResponse]]


class StatsProvider(Protocol):
    async def get_profile(self, steam64_id: str) -> Profile: ...

    async def get_recent_matches(self, steam64_id: str) -> list[Match]: ...

    async def get_match(self, game_id: str) -> Match: ...


class LeetifyClient:
    """Leetify public API client; the only CS2 module doing HTTP."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        fetch: FetchFn | None = None,
        timeout_s: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._fetch = fetch or self._aiohttp_fetch
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def get_profile(self, steam64_id: str) -> Profile:
        return parse_profile(await self._get_object("/v3/profile", {"steam64_id": steam64_id}))

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        """Recent matches, newest first; unparseable entries are skipped."""
        body = await self._get("/v3/profile/matches", {"steam64_id": steam64_id})
        if not isinstance(body, list):
            raise ApiError()
        matches = []
        for entry in body:
            if not isinstance(entry, dict):
                continue
            try:
                matches.append(parse_match(entry))
            except ApiError:
                continue
        return newest_first(matches)

    async def get_match(self, game_id: str) -> Match:
        path = f"/v2/matches/{quote(game_id, safe='')}"
        return parse_match(await self._get_object(path, not_found=MatchNotFoundError))

    async def aclose(self) -> None:
        """Close the lazily created aiohttp session (no-op with an injected fetch)."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get_object(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        not_found: type[Cs2Error] = PlayerNotFoundError,
    ) -> dict[str, Any]:
        body = await self._get(path, params, not_found)
        if not isinstance(body, dict):
            raise ApiError()
        return body

    async def _get(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        not_found: type[Cs2Error] = PlayerNotFoundError,
    ) -> Any:
        url = self._base_url + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._fetch(url, headers)
        except TimeoutError:
            log.warning("Leetify request timed out: %s", path)
            raise ApiError() from None
        except (aiohttp.ClientError, OSError, UnicodeDecodeError) as exc:
            log.warning("Leetify request failed: %s (%s)", path, type(exc).__name__)
            raise ApiError() from None
        _raise_for_status(response.status, path, not_found)
        try:
            return json.loads(response.body)
        except ValueError:
            log.warning("Leetify sent non-JSON body: %s", path)
            raise ApiError() from None

    async def _aiohttp_fetch(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_s)
            )
        async with self._session.get(url, headers=dict(headers)) as response:
            return FetchResponse(response.status, await response.text())


def _raise_for_status(
    status: int, path: str, not_found: type[Cs2Error] = PlayerNotFoundError
) -> None:
    if 200 <= status < 300:
        return
    log.warning("Leetify returned HTTP %d: %s", status, path)
    if status == 404:
        raise not_found()
    if status == 403:
        raise PrivateProfileError()
    if status == 429:
        raise RateLimitedError()
    raise ApiError()
