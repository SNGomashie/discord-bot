import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from ninja_bot.cs2.client import DEFAULT_BASE_URL, FetchResponse, LeetifyClient, StatsProvider
from ninja_bot.cs2.models import (
    ApiError,
    Cs2Error,
    MatchNotFoundError,
    PlayerNotFoundError,
    PrivateProfileError,
    RateLimitedError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "leetify"
MATCH_JSON = (FIXTURES / "match.json").read_text()
PROFILE_JSON = (FIXTURES / "profile.json").read_text()
STEAM_ID = "76561198000000001"
SECRET = "super-secret-key-123"


class StubFetch:
    def __init__(self, status: int = 200, body: str = "{}") -> None:
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


def _client(fetch: StubFetch, api_key: str | None = SECRET, **kwargs: Any) -> LeetifyClient:
    return LeetifyClient(api_key, fetch=fetch, **kwargs)


def _match(match_id: str, finished_at: str | None) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(MATCH_JSON)
    raw["id"] = match_id
    raw["finished_at"] = finished_at
    return raw


def test_client_satisfies_protocol() -> None:
    provider: StatsProvider = LeetifyClient()
    assert isinstance(provider, LeetifyClient)


async def test_get_profile() -> None:
    fetch = StubFetch(body=PROFILE_JSON)
    profile = await _client(fetch).get_profile(STEAM_ID)

    assert profile.steam64_id
    [(url, headers)] = fetch.calls
    assert url == f"{DEFAULT_BASE_URL}/v3/profile?steam64_id={STEAM_ID}"
    assert headers["Authorization"] == f"Bearer {SECRET}"


async def test_get_match_quotes_game_id() -> None:
    fetch = StubFetch(body=MATCH_JSON)
    match = await _client(fetch).get_match("a b/../c?d")

    assert match.id == "8d5a1f3e-6b2c-4c11-9a7e-2f0c5b9d4e71"
    [(url, _)] = fetch.calls
    assert url == f"{DEFAULT_BASE_URL}/v2/matches/a%20b%2F..%2Fc%3Fd"


async def test_get_recent_matches_newest_first_and_skips_bad_entries() -> None:
    body = [
        _match("old", "2026-10-01T10:00:00Z"),
        "not a match",
        {"id": "no-players"},
        _match("undated", None),
        _match("new", "2026-10-09T10:00:00Z"),
    ]
    fetch = StubFetch(body=json.dumps(body))
    matches = await _client(fetch).get_recent_matches(STEAM_ID)

    assert [m.id for m in matches] == ["new", "old", "undated"]
    [(url, _)] = fetch.calls
    assert url == f"{DEFAULT_BASE_URL}/v3/profile/matches?steam64_id={STEAM_ID}"


async def test_get_recent_matches_empty_list() -> None:
    assert await _client(StubFetch(body="[]")).get_recent_matches(STEAM_ID) == []


async def test_recent_matches_requires_list() -> None:
    with pytest.raises(ApiError):
        await _client(StubFetch(body="{}")).get_recent_matches(STEAM_ID)


async def test_no_authorization_header_without_key() -> None:
    fetch = StubFetch(body=PROFILE_JSON)
    await _client(fetch, api_key=None).get_profile(STEAM_ID)
    await _client(fetch, api_key="").get_profile(STEAM_ID)

    assert all("Authorization" not in headers for _, headers in fetch.calls)


async def test_custom_base_url_trailing_slash() -> None:
    fetch = StubFetch(body=MATCH_JSON)
    await _client(fetch, base_url="http://example.test/").get_match("m1")

    assert fetch.calls[0][0] == "http://example.test/v2/matches/m1"


@pytest.mark.parametrize(
    ("status", "error", "match_error"),
    [
        (404, PlayerNotFoundError, MatchNotFoundError),
        (403, PrivateProfileError, PrivateProfileError),
        (429, RateLimitedError, RateLimitedError),
        (500, ApiError, ApiError),
        (502, ApiError, ApiError),
        (401, ApiError, ApiError),
        (301, ApiError, ApiError),
    ],
)
async def test_status_mapping(
    status: int, error: type[Cs2Error], match_error: type[Cs2Error]
) -> None:
    client = _client(StubFetch(status=status, body=PROFILE_JSON))
    for call, expected in (
        (client.get_profile(STEAM_ID), error),
        (client.get_recent_matches(STEAM_ID), error),
        (client.get_match("m1"), match_error),
    ):
        with pytest.raises(expected) as info:
            await call
        assert type(info.value) is expected
        assert SECRET not in str(info.value)


async def test_unknown_match_has_match_message() -> None:
    with pytest.raises(MatchNotFoundError) as info:
        await _client(StubFetch(status=404)).get_match("nope")
    assert info.value.message == MatchNotFoundError.default_message
    assert "match" in info.value.message


@pytest.mark.parametrize("body", ["not json", "", "[1, 2", "<html>oops</html>"])
async def test_bad_json(body: str) -> None:
    with pytest.raises(ApiError):
        await _client(StubFetch(body=body)).get_profile(STEAM_ID)


@pytest.mark.parametrize("body", ["[]", "null", "42", '"text"'])
async def test_non_object_body(body: str) -> None:
    client = _client(StubFetch(body=body))
    with pytest.raises(ApiError):
        await client.get_profile(STEAM_ID)
    with pytest.raises(ApiError):
        await client.get_match("m1")


async def test_timeout() -> None:
    fetch = StubFetch(body=PROFILE_JSON)
    fetch.delay_s = 1.0
    with pytest.raises(ApiError):
        await _client(fetch, timeout_s=0.01).get_profile(STEAM_ID)


def test_default_timeout_is_ten_seconds() -> None:
    assert LeetifyClient()._timeout_s == 10.0


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError(),
        aiohttp.ClientConnectionError(f"boom {SECRET}"),
        aiohttp.ClientPayloadError("bad payload"),
        OSError("network unreachable"),
        UnicodeDecodeError("utf-8", SECRET.encode() + b"\xff", len(SECRET), len(SECRET) + 1, "bad"),
    ],
)
async def test_connection_errors_become_api_error(exc: BaseException) -> None:
    fetch = StubFetch()
    fetch.error = exc
    with pytest.raises(ApiError) as info:
        await _client(fetch).get_profile(STEAM_ID)

    assert info.value.message == ApiError.default_message
    assert info.value.__cause__ is None
    assert SECRET not in str(info.value)


async def test_undecodable_body_logs_warning_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    fetch = StubFetch()
    fetch.error = UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
    with pytest.raises(ApiError) as info:
        await _client(fetch).get_profile(STEAM_ID)

    assert info.value.message == ApiError.default_message
    assert info.value.__cause__ is None
    [record] = [r for r in caplog.records if r.name == "ninja_bot.cs2.client"]
    assert record.levelname == "WARNING"
    assert record.exc_info is None
    assert "UnicodeDecodeError" in record.getMessage()
    assert SECRET not in caplog.text


async def test_key_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    for fetch in (StubFetch(status=500), StubFetch(body="nope")):
        with pytest.raises(ApiError):
            await _client(fetch).get_profile(STEAM_ID)
    fetch = StubFetch()
    fetch.error = aiohttp.ClientConnectionError("down")
    with pytest.raises(ApiError):
        await _client(fetch).get_profile(STEAM_ID)

    assert caplog.records
    assert SECRET not in caplog.text


async def test_aclose_without_session_is_noop() -> None:
    client = LeetifyClient()
    await client.aclose()
    await client.aclose()


async def test_aclose_with_injected_fetch_is_noop() -> None:
    fetch = StubFetch(body=MATCH_JSON)
    client = _client(fetch)
    await client.get_match("m1")
    await client.aclose()
    await client.aclose()
    assert client._session is None
    await client.get_match("m1")
    assert len(fetch.calls) == 2


async def test_default_fetch_creates_and_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        async def text(self) -> str:
            return MATCH_JSON

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeSession:
        closed = False

        def __init__(self, timeout: aiohttp.ClientTimeout) -> None:
            seen["timeout"] = timeout
            seen["sessions"] = seen.get("sessions", 0) + 1

        def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            seen["url"], seen["headers"] = url, headers
            return FakeResponse()

        async def close(self) -> None:
            self.closed = True
            seen["closed"] = True

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    client = LeetifyClient(SECRET)
    await client.get_match("m1")
    await client.get_match("m1")
    await client.aclose()

    assert seen["sessions"] == 1
    assert seen["timeout"].total == 10.0
    assert seen["url"] == f"{DEFAULT_BASE_URL}/v2/matches/m1"
    assert seen["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert seen["closed"] is True
