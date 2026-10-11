"""Session slot input and candidate slots (no discord import)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Longest accepted `days` / `times` text; also the max length of the command options.
MAX_DAYS_LENGTH = 64
MAX_TIMES_LENGTH = 64

INVALID_DAYS_MESSAGE = (
    "Use weekdays like `fri,sat` or `friday, saturday` (or `weekend` / `weekdays`)."
)
INVALID_TIMES_MESSAGE = "Use 24-hour times like `19:00,14:30`."
INVALID_START_DATE_MESSAGE = "Use a start date like `2026-11-06` (YYYY-MM-DD)."
PAST_START_DATE_MESSAGE = "The start date can't be in the past."
NO_SLOTS_MESSAGE = "There are no slots in that window; pick a later start date or more weeks."
TOO_MANY_SLOTS_MESSAGE = "That gives {count} slots (max {max}); use fewer weeks, days or times."

# Most candidate slots in one round (Discord select menus hold 25 options).
MAX_SLOTS = 25
MAX_WEEKS = 8
DEFAULT_WEEKS = 2
DEFAULT_DURATION_HOURS = 4

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAYS: dict[str, tuple[int, ...]] = {
    **{name: (number,) for number, name in enumerate(_DAY_NAMES)},
    **{name[:3]: (number,) for number, name in enumerate(_DAY_NAMES)},
    "weekend": (5, 6),
    "weekdays": (0, 1, 2, 3, 4),
}

# re.ASCII: `\d` must not match other Unicode digits.
_TIME_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)", re.ASCII)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


@dataclass(frozen=True)
class Slot:
    """A candidate session time in UTC Unix seconds (`end = start + duration`)."""

    start: float
    end: float


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


def parse_start_date(text: str, tz: ZoneInfo, now: float) -> date:
    """Parse `YYYY-MM-DD`; blank means tomorrow in `tz`. Dates before today are rejected."""
    today = datetime.fromtimestamp(now, tz).date()
    text = text.strip()
    if not text:
        return today + timedelta(days=1)
    if _DATE_RE.fullmatch(text) is None:
        raise InvalidSessionInput(INVALID_START_DATE_MESSAGE)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise InvalidSessionInput(INVALID_START_DATE_MESSAGE) from None
    if parsed < today:
        raise InvalidSessionInput(PAST_START_DATE_MESSAGE)
    return parsed


def next_window_start(previous_start: date, weeks: int) -> date:
    """The first day after the window `[previous_start, previous_start + weeks)`."""
    return previous_start + timedelta(weeks=weeks)


def _local_start(day: date, at: time, tz: ZoneInfo) -> datetime | None:
    # fold=0 picks the first occurrence of a repeated (fall-back) time; a time in
    # the spring-forward gap doesn't survive the round trip through UTC.
    local = datetime.combine(day, at, tzinfo=tz)
    if datetime.fromtimestamp(local.timestamp(), tz).replace(tzinfo=None) != (
        local.replace(tzinfo=None)
    ):
        return None
    return local


def generate_slots(
    start_date: date,
    weeks: int,
    days: tuple[int, ...],
    times: tuple[time, ...],
    tz: ZoneInfo,
    duration_hours: float,
    now: float,
) -> list[Slot]:
    """Candidate slots in `[start_date, start_date + weeks * 7 days)`, oldest first.

    Each slot starts at the local wall-clock time on a chosen weekday. DST: a time
    that doesn't exist that day (spring-forward gap) is skipped, not shifted; a
    repeated time (fall-back) gives one slot at its first occurrence (`fold=0`).
    Slots that don't start after `now` are dropped. Raises `InvalidSessionInput`
    when nothing is left or when there are more than `MAX_SLOTS`.
    """
    duration = duration_hours * 3600
    wanted = set(days)
    starts: list[float] = []
    for offset in range(weeks * 7):
        day = start_date + timedelta(days=offset)
        if day.weekday() not in wanted:
            continue
        for at in times:
            local = _local_start(day, at, tz)
            if local is not None and local.timestamp() > now:
                starts.append(local.timestamp())
    if not starts:
        raise InvalidSessionInput(NO_SLOTS_MESSAGE)
    if len(starts) > MAX_SLOTS:
        raise InvalidSessionInput(TOO_MANY_SLOTS_MESSAGE.format(count=len(starts), max=MAX_SLOTS))
    return [Slot(start, start + duration) for start in sorted(starts)]
