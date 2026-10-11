"""Session slot input: weekday and time-of-day parsing (no discord import)."""

from __future__ import annotations

import re
from datetime import time

# Longest accepted `days` / `times` text; also the max length of the command options.
MAX_DAYS_LENGTH = 64
MAX_TIMES_LENGTH = 64

INVALID_DAYS_MESSAGE = (
    "Use weekdays like `fri,sat` or `friday, saturday` (or `weekend` / `weekdays`)."
)
INVALID_TIMES_MESSAGE = "Use 24-hour times like `19:00,14:30`."

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAYS: dict[str, tuple[int, ...]] = {
    **{name: (number,) for number, name in enumerate(_DAY_NAMES)},
    **{name[:3]: (number,) for number, name in enumerate(_DAY_NAMES)},
    "weekend": (5, 6),
    "weekdays": (0, 1, 2, 3, 4),
}

# re.ASCII: `\d` must not match other Unicode digits.
_TIME_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)", re.ASCII)


class InvalidSessionInput(ValueError):
    """Session poll input that can't be parsed; `message` is user-facing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _split(text: str, max_length: int, message: str) -> list[str]:
    # The length cap runs before any splitting so huge inputs can't stall.
    if len(text) > max_length:
        raise InvalidSessionInput(message)
    parts = [part.strip() for part in text.split(",")]
    if not any(parts) or not all(parts):
        raise InvalidSessionInput(message)
    return parts


def parse_days(text: str) -> tuple[int, ...]:
    """Parse `fri,sat`, `Friday, Saturday`, `weekend`, `weekdays` into weekdays (Mon=0)."""
    days: set[int] = set()
    for part in _split(text, MAX_DAYS_LENGTH, INVALID_DAYS_MESSAGE):
        found = _DAYS.get(part.lower())
        if found is None:
            raise InvalidSessionInput(INVALID_DAYS_MESSAGE)
        days.update(found)
    return tuple(sorted(days))


def parse_times(text: str) -> tuple[time, ...]:
    """Parse `9:00`, `19:30` (comma-separated, 24-hour) into sorted naive times."""
    times: set[time] = set()
    for part in _split(text, MAX_TIMES_LENGTH, INVALID_TIMES_MESSAGE):
        found = _TIME_RE.fullmatch(part)
        if found is None:
            raise InvalidSessionInput(INVALID_TIMES_MESSAGE)
        times.add(time(int(found.group(1)), int(found.group(2))))
    return tuple(sorted(times))
