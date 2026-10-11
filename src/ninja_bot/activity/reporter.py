"""Which month's overview to auto-post next; no Discord, injectable clock."""

from __future__ import annotations

import time
from collections.abc import Callable
from zoneinfo import ZoneInfo

from ninja_bot.activity.periods import Month, month_of
from ninja_bot.activity.stats import clip
from ninja_bot.activity.store import ActivityStore


class MonthlyReporter:
    """Picks the oldest ended, unposted month with voice time; `confirm` marks it posted."""

    def __init__(
        self, store: ActivityStore, tz: ZoneInfo, clock: Callable[[], float] = time.time
    ) -> None:
        self.store = store
        self.tz = tz
        self.clock = clock

    async def due_month(self) -> Month | None:
        """The oldest month that has ended locally, has voice time and wasn't posted.

        Never older than the month of the first recorded session; at most one per call.
        """
        first = await self.store.first_session_start()
        if first is None:
            return None
        now = self.clock()
        current = month_of(now, self.tz)
        month = month_of(first, self.tz)
        while month < current:
            if not await self.store.report_posted(month.key) and await self._has_data(month, now):
                return month
            month = month.next()
        return None

    async def confirm(self, month: Month) -> None:
        """Mark `month` posted; call after every send attempt, even a failed one."""
        await self.store.mark_report_posted(month.key, self.clock())

    async def _has_data(self, month: Month, now: float) -> bool:
        start = month.start(self.tz).timestamp()
        end = month.end(self.tz).timestamp()
        sessions = await self.store.sessions_between(start, end)
        return any(clip(session, start, end, now) is not None for session in sessions)
