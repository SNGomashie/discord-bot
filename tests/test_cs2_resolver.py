import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from ninja_bot.cs2.client import FetchResponse
from ninja_bot.cs2.models import (
    Cs2Error,
    InvalidSteamIdError,
    RateLimitedError,
    SteamLookupError,
    VanityNotFoundError,
)
from ninja_bot.cs2.resolver import (
    MAX_BODY_BYTES,
    STEAM_RATE_LIMITED_MESSAGE,
    SteamCommunityResolver,
    SteamResolver,
)

FIXTURES = Path(__file__).parent / "fixtures" / "steam"
PROFILE_XML = (FIXTURES / "profile.xml").read_text()
NOT_FOUND_XML = (FIXTURES / "profile_not_found.xml").read_text()
STEAM_ID = "76561197960287930"
OTHER_ID = "76561198000000001"


class StubFetch:
    def __init__(self, status: int = 200, body: str = PROFILE_XML) -> None:
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


def test_resolver_satisfies_protocol() -> None:
    resolver: SteamResolver = SteamCommunityResolver()
    assert isinstance(resolver, SteamCommunityResolver)


async def test_public_profile_resolves_and_requests_exact_url() -> None:
    fetch = StubFetch()
    assert await SteamCommunityResolver(fetch).resolve_vanity("gabeLoganNewell") == STEAM_ID
    [(url, _)] = fetch.calls
    assert url == "https://steamcommunity.com/id/gabeLoganNewell/?xml=1"


async def test_private_profile_still_resolves() -> None:
    body = PROFILE_XML.replace("<privacyState>public", "<privacyState>private")
    assert "private" in body
    fetch = StubFetch(body=body)
    assert await SteamCommunityResolver(fetch).resolve_vanity("gaben") == STEAM_ID


async def test_first_id_wins() -> None:
    body = f"<profile><steamID64>{OTHER_ID}</steamID64><steamID64>{STEAM_ID}</steamID64>"
    fetch = StubFetch(body=body)
    assert await SteamCommunityResolver(fetch).resolve_vanity("gaben") == OTHER_ID


@pytest.mark.parametrize(
    "body",
    [
        NOT_FOUND_XML,
        "<profile><steamID>No id here</steamID></profile>",
        "<profile><steamID64>12345678901234567</steamID64></profile>",
        "",
    ],
)
async def test_body_without_id_is_not_found(body: str) -> None:
    with pytest.raises(VanityNotFoundError):
        await SteamCommunityResolver(StubFetch(body=body)).resolve_vanity("gaben")


async def test_id_after_body_cap_is_not_found() -> None:
    body = "<profile>" + " " * MAX_BODY_BYTES + f"<steamID64>{STEAM_ID}</steamID64></profile>"
    with pytest.raises(VanityNotFoundError):
        await SteamCommunityResolver(StubFetch(body=body)).resolve_vanity("gaben")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, VanityNotFoundError),
        (429, RateLimitedError),
        (301, SteamLookupError),
        (302, SteamLookupError),
        (403, SteamLookupError),
        (500, SteamLookupError),
        (503, SteamLookupError),
    ],
)
async def test_status_mapping(status: int, error: type[Cs2Error]) -> None:
    # The body has an ID so only the status decides the outcome.
    fetch = StubFetch(status=status)
    with pytest.raises(error):
        await SteamCommunityResolver(fetch).resolve_vanity("gaben")


async def test_rate_limit_message_mentions_steam() -> None:
    with pytest.raises(RateLimitedError) as info:
        await SteamCommunityResolver(StubFetch(status=429)).resolve_vanity("gaben")
    assert info.value.message == STEAM_RATE_LIMITED_MESSAGE


@pytest.mark.parametrize(
    "exc",
    [
        aiohttp.ClientError("boom"),
        aiohttp.ClientConnectionError("refused"),
        OSError("network down"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
)
async def test_fetch_errors_are_lookup_errors(
    exc: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    fetch = StubFetch()
    fetch.error = exc
    with caplog.at_level(logging.WARNING), pytest.raises(SteamLookupError) as info:
        await SteamCommunityResolver(fetch).resolve_vanity("gaben")
    assert info.value.__cause__ is None
    assert caplog.records
    assert all(record.exc_info is None for record in caplog.records)


async def test_timeout_is_lookup_error() -> None:
    fetch = StubFetch()
    fetch.delay_s = 1.0
    with pytest.raises(SteamLookupError):
        await SteamCommunityResolver(fetch, timeout_s=0.01).resolve_vanity("gaben")


async def test_logs_never_include_body(caplog: pytest.LogCaptureFixture) -> None:
    marker = "SECRET-BODY-MARKER"
    for status in (200, 500):
        fetch = StubFetch(status=status, body=f"<profile>{marker}</profile>")
        with caplog.at_level(logging.DEBUG), pytest.raises(Cs2Error):
            await SteamCommunityResolver(fetch).resolve_vanity("gaben")
    assert caplog.records
    assert marker not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    "name", ["", "a", "a" * 33, "gabe newell", "../admin", "gabe/x", "gabe?x=1", "név", STEAM_ID]
)
async def test_invalid_name_makes_no_request(name: str) -> None:
    fetch = StubFetch()
    with pytest.raises(InvalidSteamIdError):
        await SteamCommunityResolver(fetch).resolve_vanity(name)
    assert fetch.calls == []


async def test_aclose_without_session_is_noop() -> None:
    resolver = SteamCommunityResolver()
    await resolver.aclose()
    await resolver.aclose()


class FakeContent:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.read_sizes: list[int] = []

    async def read(self, n: int) -> bytes:
        self.read_sizes.append(n)
        return self.data[:n]


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self.status = status
        self.content = FakeContent(data)

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _fake_session(
    monkeypatch: pytest.MonkeyPatch, data: bytes, seen: dict[str, Any]
) -> list[FakeResponse]:
    responses: list[FakeResponse] = []

    class FakeSession:
        closed = False

        def __init__(self, timeout: aiohttp.ClientTimeout) -> None:
            seen["timeout"] = timeout
            seen["sessions"] = seen.get("sessions", 0) + 1

        def get(self, url: str, headers: dict[str, str], allow_redirects: bool) -> FakeResponse:
            seen["url"], seen["allow_redirects"] = url, allow_redirects
            responses.append(FakeResponse(data))
            return responses[-1]

        async def close(self) -> None:
            self.closed = True
            seen["closes"] = seen.get("closes", 0) + 1

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    return responses


async def test_default_fetch_creates_and_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    responses = _fake_session(monkeypatch, PROFILE_XML.encode(), seen)
    resolver = SteamCommunityResolver()
    assert await resolver.resolve_vanity("gaben") == STEAM_ID
    assert await resolver.resolve_vanity("gaben") == STEAM_ID
    await resolver.aclose()
    await resolver.aclose()

    assert seen["sessions"] == 1
    assert seen["closes"] == 1
    assert seen["timeout"].total == 10.0
    assert seen["url"] == "https://steamcommunity.com/id/gaben/?xml=1"
    assert seen["allow_redirects"] is False
    assert all(r.content.read_sizes == [MAX_BODY_BYTES] for r in responses)


async def test_default_fetch_caps_body(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b" " * MAX_BODY_BYTES + f"<steamID64>{STEAM_ID}</steamID64>".encode()
    _fake_session(monkeypatch, data, {})
    resolver = SteamCommunityResolver()
    with pytest.raises(VanityNotFoundError):
        await resolver.resolve_vanity("gaben")
    await resolver.aclose()


async def test_default_fetch_tolerates_cut_multibyte_char(monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = f"<steamID64>{STEAM_ID}</steamID64>".encode()
    padding = b" " * (MAX_BODY_BYTES - len(prefix) - 1)
    _fake_session(monkeypatch, prefix + padding + "é".encode(), {})
    resolver = SteamCommunityResolver()
    assert await resolver.resolve_vanity("gaben") == STEAM_ID
    await resolver.aclose()


async def test_default_fetch_invalid_utf8_is_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session(monkeypatch, b"<profile>\xff\xfe</profile>", {})
    resolver = SteamCommunityResolver()
    with pytest.raises(SteamLookupError):
        await resolver.resolve_vanity("gaben")
    await resolver.aclose()
