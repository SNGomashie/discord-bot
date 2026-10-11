"""Text helpers for activity reports (no discord import)."""

from __future__ import annotations

import math

ACTIVE_DAY = "🟩"
INACTIVE_DAY = "⬜"
DAYS_PER_GROUP = 7


def format_hours(seconds: float) -> str:
    """`0m`, `45m`, `1h 05m`, `103h 07m`; truncates to whole minutes, never negative."""
    if not math.isfinite(seconds) or seconds <= 0:
        return "0m"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    return f"{hours}h {minutes:02d}m"


def day_strip(active_days: set[int], days_in_month: int) -> str:
    """One cell per day (1-based), a space between each group of 7 days."""
    cells = [
        ACTIVE_DAY if day in active_days else INACTIVE_DAY for day in range(1, days_in_month + 1)
    ]
    groups = ["".join(cells[i : i + DAYS_PER_GROUP]) for i in range(0, len(cells), DAYS_PER_GROUP)]
    return " ".join(groups)


MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def month_label(year: int, month: int) -> str:
    """`September 2026` (English, independent of the system locale)."""
    return f"{MONTH_NAMES[month - 1]} {year}"


def hour_range(hour: int) -> str:
    """`21:00–22:00` for local hour 21; the last hour ends at `24:00`."""
    return f"{hour:02d}:00–{hour + 1:02d}:00"


def plural(count: int, word: str) -> str:
    """`1 day`, `3 days`."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
