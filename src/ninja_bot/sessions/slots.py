"""Session slot input and candidate slots (no discord import)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Longest accepted `days` / `times` text; also the max length of the command options.
MAX_DAYS_LENGTH = 64
MAX_TIMES_LENGTH = 64
MAX_EXCLUDE_LENGTH = 200
# Longest single excluded range, in days (a range is expanded day by day).
MAX_EXCLUDED_RANGE_DAYS = 62

INVALID_DAYS_MESSAGE = (
    "Use weekdays like `fri,sat` or `friday, saturday` (or `weekend` / `weekdays`)."
)
INVALID_TIMES_MESSAGE = "Use 24-hour times like `19:00,14:30`."
INVALID_START_DATE_MESSAGE = "Use a start date like `2026-11-06` (YYYY-MM-DD)."
INVALID_EXCLUDE_MESSAGE = (
    "Use dates or ranges like `2026-11-05,2026-11-13..2026-11-15` (YYYY-MM-DD,"
    f" at most {MAX_EXCLUDED_RANGE_DAYS} days per range)."
)
PAST_START_DATE_MESSAGE = "The start date can't be in the past."
NO_SLOTS_MESSAGE = "There are no slots in that window; pick a later start date or more weeks."
TOO_MANY_SLOTS_MESSAGE = "That gives {count} slots (max {max}); use fewer weeks, days or times."

# Most candidate slots in one round (Discord select menus hold 25 options).
MAX_SLOTS = 25
MAX_WEEKS = 8
DEFAULT_WEEKS = 2
DEFAULT_DURATION_HOURS = 4

_SHORT_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SHORT_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
)  # fmt: skip

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


# An excluded run of days, both ends included.
DateRange = tuple[date, date]


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


def _parse_iso_date(text: str, message: str) -> date:
    if _DATE_RE.fullmatch(text) is None:
        raise InvalidSessionInput(message)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise InvalidSessionInput(message) from None


def parse_start_date(text: str, tz: ZoneInfo, now: float) -> date:
    """Parse `YYYY-MM-DD`; blank means tomorrow in `tz`. Dates before today are rejected."""
    today = datetime.fromtimestamp(now, tz).date()
    text = text.strip()
    if not text:
        return today + timedelta(days=1)
    parsed = _parse_iso_date(text, INVALID_START_DATE_MESSAGE)
    if parsed < today:
        raise InvalidSessionInput(PAST_START_DATE_MESSAGE)
    return parsed


def parse_excluded(text: str) -> tuple[DateRange, ...]:
    """Parse `2026-11-05,2026-11-13..2026-11-15` into sorted, de-duplicated date ranges.

    Blank means no exclusions. Each item is one day or `first..last` (both included).
    """
    text = text.strip()
    if not text:
        return ()
    ranges: set[DateRange] = set()
    for part in _split(text, MAX_EXCLUDE_LENGTH, INVALID_EXCLUDE_MESSAGE):
        first, dots, last = part.partition("..")
        start = _parse_iso_date(first.strip(), INVALID_EXCLUDE_MESSAGE)
        end = _parse_iso_date(last.strip(), INVALID_EXCLUDE_MESSAGE) if dots else start
        if end < start or (end - start).days >= MAX_EXCLUDED_RANGE_DAYS:
            raise InvalidSessionInput(INVALID_EXCLUDE_MESSAGE)
        ranges.add((start, end))
    return tuple(sorted(ranges))


def format_excluded(ranges: Sequence[DateRange]) -> str:
    """`Thu 22 Oct - Sat 24 Oct, Thu 5 Nov` for display."""
    return ", ".join(
        format_local_date(first)
        if first == last
        else f"{format_local_date(first)} - {format_local_date(last)}"
        for first, last in ranges
    )


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
    excluded: Sequence[DateRange] = (),
) -> list[Slot]:
    """Candidate slots in `[start_date, start_date + weeks * 7 days)`, oldest first.

    Days inside an `excluded` range get no slots.

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
        if day.weekday() not in wanted or any(first <= day <= last for first, last in excluded):
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


def format_local_date(day: date) -> str:
    """`Fri 6 Nov`, with English names regardless of the locale."""
    return f"{_SHORT_WEEKDAYS[day.weekday()]} {day.day} {_SHORT_MONTHS[day.month - 1]}"


def format_local_time(unix: float, tz: ZoneInfo) -> str:
    """`Fri 6 Nov 19:00` in `tz`, with English names regardless of the locale."""
    local = datetime.fromtimestamp(unix, tz)
    return f"{format_local_date(local.date())} {local:%H:%M}"
