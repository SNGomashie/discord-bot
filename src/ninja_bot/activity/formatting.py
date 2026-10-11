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
