from __future__ import annotations

import pytest

from ninja_bot.help.lookup import (
    AUTOCOMPLETE_LIMIT,
    autocomplete_names,
    find_command,
    normalize_name,
    suggest,
)

NAMES = ("ping", "download", "link", "unlink", "scoreboard", "profile", "leaderboard", "help")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("download", "download"),
        ("/Download", "download"),
        (" download ", "download"),
        ("DOWNLOAD", "download"),
        ("  /PING\t", "ping"),
        ("//ping", "/ping"),
        ("", ""),
        ("   ", ""),
        ("/", ""),
    ],
)
def test_normalize_name(text: str, expected: str) -> None:
    assert normalize_name(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/Download", "download"),
        (" download ", "download"),
        ("DOWNLOAD", "download"),
        ("unlink", "unlink"),
        ("downloads", None),
        ("down", None),
        ("nope", None),
        ("", None),
        ("   ", None),
        ("/", None),
        ("//ping", None),
    ],
)
def test_find_command(text: str, expected: str | None) -> None:
    assert find_command(text, NAMES) == expected


def test_find_command_normalises_names_too() -> None:
    assert find_command("ping", ("/Ping",)) == "/Ping"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("link", ["link", "unlink"]),
        ("/LINK", ["link", "unlink"]),
        ("o", ["download", "leaderboard", "profile"]),
        ("pro", ["profile"]),
        ("board", ["leaderboard", "scoreboard"]),
        ("xyz", []),
        ("", ["download", "help", "leaderboard"]),
        ("   ", ["download", "help", "leaderboard"]),
        ("/", ["download", "help", "leaderboard"]),
    ],
)
def test_suggest(text: str, expected: list[str]) -> None:
    assert suggest(text, NAMES) == expected


def test_suggest_prefix_before_substring() -> None:
    assert suggest("l", NAMES, limit=10) == [
        "leaderboard",
        "link",
        "download",
        "help",
        "profile",
        "unlink",
    ]


@pytest.mark.parametrize("limit", [0, 1, 2, 5])
def test_suggest_respects_limit(limit: int) -> None:
    assert len(suggest("", NAMES, limit=limit)) == min(limit, len(NAMES))


def test_suggest_no_duplicates() -> None:
    assert suggest("link", ("link", "unlink", "link"), limit=5) == ["link", "unlink"]


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("", sorted(NAMES)),
        ("  ", sorted(NAMES)),
        ("/", sorted(NAMES)),
        ("link", ["link", "unlink"]),
        ("/Li", ["link", "unlink"]),
        ("e", ["help", "leaderboard", "profile", "scoreboard"]),
        ("zzz", []),
    ],
)
def test_autocomplete_names(current: str, expected: list[str]) -> None:
    assert autocomplete_names(current, NAMES) == expected


def test_autocomplete_caps_at_discord_maximum() -> None:
    names = [f"cmd{i:02}" for i in range(40)]
    result = autocomplete_names("", names, limit=100)
    assert len(result) == AUTOCOMPLETE_LIMIT == 25
    assert result == sorted(names)[:25]


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_autocomplete_respects_limit(limit: int) -> None:
    assert autocomplete_names("", NAMES, limit=limit) == sorted(NAMES)[:limit]


def test_autocomplete_no_duplicates() -> None:
    assert autocomplete_names("", ("help", "ping", "help")) == ["help", "ping"]
