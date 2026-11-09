"""Intro and roast text above the monthly overview (no discord import)."""

from __future__ import annotations

from collections.abc import Sequence

from ninja_bot.activity.content import INTRO_LINES, ROAST_FIRST, ROAST_SECOND, ROAST_THIRD
from ninja_bot.activity.formatting import format_hours
from ninja_bot.activity.report import MonthlyReport
from ninja_bot.flavor import LinePicker

ROASTED_RANKS = 3
_ROAST_POOLS: dict[int, Sequence[str]] = {1: ROAST_FIRST, 2: ROAST_SECOND, 3: ROAST_THIRD}
MAX_MESSAGE_LENGTH = 2000


def monthly_message(report: MonthlyReport, picker: LinePicker) -> str:
    """Intro line plus one roast per leaderboard row ranked 1-3 (ties roast everyone tied)."""
    lines = [picker.pick(INTRO_LINES)]
    for row in report.leaderboard:
        if row.rank > ROASTED_RANKS:
            break
        lines.append(
            picker.pick(
                _ROAST_POOLS[row.rank], user=f"<@{row.user_id}>", hours=format_hours(row.value)
            )
        )
    text = "\n".join(lines)
    return text[:MAX_MESSAGE_LENGTH]
