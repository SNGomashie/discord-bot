"""The optional player list of a session poll (no discord import)."""

from __future__ import annotations

import re

from ninja_bot.sessions.slots import InvalidSessionInput

# Longest accepted `players` text; also the max length of the command option.
MAX_PLAYERS_TEXT_LENGTH = 800

INVALID_PLAYERS_MESSAGE = "Mention the players like `@Alice @Bob`, with no other text."

# re.ASCII: `\d` must not match other Unicode digits.
_MENTION_RE = re.compile(r"<@!?(\d{1,20})>", re.ASCII)


def parse_players(text: str) -> tuple[int, ...]:
    """User ids from mentions (`<@id>`, as a slash command option delivers `@name`).

    Blank means no list. Anything besides mentions, spaces and commas is rejected, so a
    typed name never silently drops a player. Duplicates are removed, order is kept.
    """
    text = text.strip()
    if not text:
        return ()
    if len(text) > MAX_PLAYERS_TEXT_LENGTH:
        raise InvalidSessionInput(INVALID_PLAYERS_MESSAGE)
    if _MENTION_RE.sub("", text).strip(" ,;\n"):
        raise InvalidSessionInput(INVALID_PLAYERS_MESSAGE)
    ids = dict.fromkeys(int(found) for found in _MENTION_RE.findall(text))
    if not ids:
        raise InvalidSessionInput(INVALID_PLAYERS_MESSAGE)
    return tuple(ids)
