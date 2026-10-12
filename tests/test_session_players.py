from __future__ import annotations

import pytest

from ninja_bot.sessions.players import (
    INVALID_PLAYERS_MESSAGE,
    MAX_PLAYERS_TEXT_LENGTH,
    parse_players,
)
from ninja_bot.sessions.slots import InvalidSessionInput


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        ("   ", ()),
        ("<@1>", (1,)),
        ("<@1> <@2>", (1, 2)),
        ("<@!1>,<@2>", (1, 2)),
        ("  <@2>,  <@1> ;<@3>\n", (2, 1, 3)),
        ("<@1> <@2> <@1>", (1, 2)),
    ],
)
def test_parse_players_accepts(text: str, expected: tuple[int, ...]) -> None:
    assert parse_players(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Alice",
        "<@1> Bob",
        "<@&5>",
        "<#5>",
        "@everyone",
        ",",
        "<@١٢٣>",
        "<@" + "9" * 21 + ">",
    ],
)
def test_parse_players_rejects_anything_but_mentions(text: str) -> None:
    with pytest.raises(InvalidSessionInput) as error:
        parse_players(text)
    assert error.value.message == INVALID_PLAYERS_MESSAGE


def test_parse_players_length_cap() -> None:
    with pytest.raises(InvalidSessionInput):
        parse_players("<@1>" * (MAX_PLAYERS_TEXT_LENGTH // 4 + 1))
