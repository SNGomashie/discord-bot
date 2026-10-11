"""Timezone-aware calendar months: the one definition of "a month"."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

MONTH_RE = re.compile(r"(\d{4})-(\d{2})", re.ASCII)
INVALID_MONTH_MESSAGE = "Use a month like `2026-09` (YYYY-MM)."
FUTURE_MONTH_MESSAGE = "That month hasn't started yet."
# Discord launched in 2015; earlier months are rejected like bad text. Leaves a
# year of room above the `Month` lower bound, so `previous()` never fails and
# local timestamps never overflow for an accepted month.
MIN_YEAR = 2015


class InvalidMonthError(ValueError):
    """Month text that isn't `YYYY-MM`; `message` is user-facing."""

    def __init__(self, message: str = INVALID_MONTH_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise InvalidMonthError()
        if not 1 <= self.year <= 9998:
            raise InvalidMonthError()

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def start(self, tz: ZoneInfo) -> datetime:
        """First local instant of the month."""
        return datetime(self.year, self.month, 1, tzinfo=tz)

    def end(self, tz: ZoneInfo) -> datetime:
        """First local instant of the next month (exclusive)."""
        return self.next().start(tz)

    def days(self, tz: ZoneInfo) -> int:
        """Number of local calendar days in the month."""
        del tz  # calendar days are the same in every timezone
        return calendar.monthrange(self.year, self.month)[1]

    def previous(self) -> Month:
        if self.month == 1:
            return Month(self.year - 1, 12)
        return Month(self.year, self.month - 1)

    def next(self) -> Month:
        if self.month == 12:
            return Month(self.year + 1, 1)
        return Month(self.year, self.month + 1)


def month_of(timestamp: float, tz: ZoneInfo) -> Month:
    """The local month a Unix timestamp falls in."""
    local = datetime.fromtimestamp(timestamp, tz)
    return Month(local.year, local.month)


def parse_month(text: str) -> Month:
    """Parse `YYYY-MM` from `MIN_YEAR` on; raises `InvalidMonthError` otherwise."""
    found = MONTH_RE.fullmatch(text.strip())
    if found is None or int(found.group(1)) < MIN_YEAR:
        raise InvalidMonthError()
    return Month(int(found.group(1)), int(found.group(2)))


def resolve_month(text: str | None, tz: ZoneInfo, now: float) -> Month:
    """The month a command asked for: blank means the current local month.

    Raises `InvalidMonthError` for bad text or a month after the current one.
    """
    current = month_of(now, tz)
    if text is None or not text.strip():
        return current
    month = parse_month(text)
    if month > current:
        raise InvalidMonthError(FUTURE_MONTH_MESSAGE)
    return month
