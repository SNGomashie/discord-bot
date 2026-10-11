"""`/session_cancel` items: autocomplete choices and their values (no discord import)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo

from ninja_bot.sessions import content
from ninja_bot.sessions.slots import format_local_date, format_local_time
from ninja_bot.sessions.store import Poll, Session

# Discord's limits for autocomplete: choices per response and characters per choice name.
MAX_CHOICES = 25
CHOICE_NAME_LIMIT = 100

ItemKind = Literal["poll", "session"]


@dataclass(frozen=True)
class CancelItem:
    """What `/session_cancel` targets: an open poll or an upcoming session, by id."""

    kind: ItemKind
    id: int

    @property
    def value(self) -> str:
        """The autocomplete value, `poll:<id>` or `session:<id>`."""
        return f"{self.kind}:{self.id}"


def parse_cancel_item(value: str) -> CancelItem | None:
    """The item behind an autocomplete value; None for anything else (typed by hand)."""
    kind, sep, raw_id = value.strip().partition(":")
    if not sep or not raw_id.isascii() or not raw_id.isdecimal():
        return None
    if kind == "poll":
        return CancelItem("poll", int(raw_id))
    if kind == "session":
        return CancelItem("session", int(raw_id))
    return None


def cancel_choices(
    polls: Sequence[Poll],
    sessions: Sequence[Session],
    tz: ZoneInfo,
    current: str = "",
    limit: int = MAX_CHOICES,
) -> list[tuple[str, str]]:
    """(label, value) pairs for open polls, then upcoming sessions, whose label contains
    `current` (case-insensitive); at most `limit`."""
    choices = [
        (
            content.CANCEL_POLL_CHOICE.format(
                name=poll.name, date=format_local_date(poll.window_start)
            ),
            CancelItem("poll", poll.id).value,
        )
        for poll in polls
    ]
    choices += [
        (
            content.CANCEL_SESSION_CHOICE.format(
                name=session.name, start=format_local_time(session.start, tz)
            ),
            CancelItem("session", session.id).value,
        )
        for session in sessions
    ]
    needle = current.strip().casefold()
    matching = [
        (label[:CHOICE_NAME_LIMIT], value) for label, value in choices if needle in label.casefold()
    ]
    return matching[:limit]
