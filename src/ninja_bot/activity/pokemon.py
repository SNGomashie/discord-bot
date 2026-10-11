"""Toasty `;pokemon` parsing and command/reply pairing (no discord import)."""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ninja_bot.activity.store import PokemonEvent

# Remembered `;pokemon` commands expire after this long (Toasty replies within seconds).
PENDING_TTL_SECONDS = 300.0
PENDING_MAX_SIZE = 256

_COMMAND_RE = re.compile(r"; ?pokemon", re.IGNORECASE | re.ASCII)
_NAME_CHARS = r"\w .'’:♀♂-"
_CATCH_RE = re.compile(
    rf"you['’]ve caught an? (?P<shiny>shiny )?(?P<name>[^\W\d_][{_NAME_CHARS}]*?)!",
    re.IGNORECASE,
)
_COOLDOWN_RE = re.compile(
    r"you need to wait (?P<wait>[^\n]{0,40}?) before catching another pok[eé]mon",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(?:(?P<d>\d{1,4}) ?d)? ?(?:(?P<h>\d{1,4}) ?h)? ?"
    r"(?:(?P<m>\d{1,4}) ?m)? ?(?:(?P<s>\d{1,4}) ?s)?",
    re.IGNORECASE | re.ASCII,
)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


@dataclass(frozen=True)
class Catch:
    pokemon: str
    shiny: bool


@dataclass(frozen=True)
class Cooldown:
    """Toasty refused the catch; `wait_seconds` is None when the wait couldn't be read."""

    wait_seconds: int | None


def is_pokemon_command(content: str) -> bool:
    """`;pokemon` (one optional space after `;`, any case) and nothing else on the line."""
    return _COMMAND_RE.fullmatch(content.strip()) is not None


def parse_duration(text: str) -> int | None:
    """Seconds in a `1h 50m 16s` style duration, or None when it isn't one."""
    match = _DURATION_RE.fullmatch(text.strip())
    if match is None or not any(match.groupdict().values()):
        return None
    return sum(
        int(value) * _UNIT_SECONDS[unit] for unit, value in match.groupdict().items() if value
    )


def _texts(content: str, embeds: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    yield content
    for embed in embeds:
        for key in ("title", "description"):
            value = embed.get(key)
            if isinstance(value, str):
                yield value
        fields = embed.get("fields")
        if isinstance(fields, Sequence):
            for field in fields:
                if isinstance(field, Mapping) and isinstance(field.get("value"), str):
                    yield field["value"]


def parse_reply(content: str, embeds: Sequence[Mapping[str, Any]]) -> Catch | Cooldown | None:
    """A Toasty reply's catch or cooldown, from its content or embeds (`Embed.to_dict()` form).

    The mention in the text is ignored: the catcher is the author of the replied-to command.
    """
    for text in _texts(content, embeds):
        catch = _CATCH_RE.search(text)
        if catch is not None:
            name = catch["name"].strip()
            if name:
                return Catch(pokemon=name, shiny=catch["shiny"] is not None)
        cooldown = _COOLDOWN_RE.search(text)
        if cooldown is not None:
            return Cooldown(wait_seconds=parse_duration(cooldown["wait"]))
    return None


def to_event(result: Catch | Cooldown, message_id: int, user_id: int, ts: float) -> PokemonEvent:
    """The event to store for a parsed reply; a cooldown's wait time isn't kept."""
    if isinstance(result, Catch):
        return PokemonEvent(message_id, user_id, ts, "catch", result.pokemon, result.shiny)
    return PokemonEvent(message_id, user_id, ts, "cooldown")


class PendingCommands:
    """Recent `;pokemon` commands (message id -> author id), capped and expiring.

    Entries stay until they expire, so duplicate deliveries and edits of the reply
    still find the author.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = PENDING_TTL_SECONDS,
        max_size: int = PENDING_MAX_SIZE,
    ) -> None:
        self._clock = clock
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._entries: OrderedDict[int, tuple[int, float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def _expire(self, now: float) -> None:
        while self._entries:
            _, (_, added) = next(iter(self._entries.items()))
            if now - added < self._ttl:
                break
            self._entries.popitem(last=False)

    def remember(self, message_id: int, author_id: int) -> None:
        now = self._clock()
        self._expire(now)
        self._entries.pop(message_id, None)
        self._entries[message_id] = (author_id, now)
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)

    def author_of(self, message_id: int) -> int | None:
        self._expire(self._clock())
        entry = self._entries.get(message_id)
        return None if entry is None else entry[0]
