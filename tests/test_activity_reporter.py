"""MonthlyReporter: which ended month's overview is due, with a fake clock."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from ninja_bot.activity.periods import Month
from ninja_bot.activity.reporter import MonthlyReporter
from ninja_bot.activity.store import ActivityStore, Session, SqliteActivityStore

UTC_TZ = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")
USER = 1
CHANNEL = 10


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def ts(year: int, month: int, day: int, hour: int = 12) -> float:
    return datetime(year, month, day, hour, tzinfo=UTC).timestamp()


def make_store(tmp_path: Path) -> SqliteActivityStore:
    return SqliteActivityStore(tmp_path / "activity.sqlite3")


async def add_session(store: SqliteActivityStore, start: float, hours: float = 1) -> None:
    await store.open_session(USER, CHANNEL, start)
    await store.close_session(USER, start + hours * 3600)


async def test_nothing_due_without_sessions(tmp_path: Path) -> None:
    reporter = MonthlyReporter(make_store(tmp_path), UTC_TZ, FakeClock(ts(2026, 11, 5)))
    assert await reporter.due_month() is None


async def test_current_month_is_not_due(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 10, 3))
    reporter = MonthlyReporter(store, UTC_TZ, FakeClock(ts(2026, 10, 31, 23)))
    assert await reporter.due_month() is None


async def test_due_after_local_midnight(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 10, 3))
    clock = FakeClock(datetime(2026, 10, 31, 23, 59, tzinfo=NEW_YORK).timestamp())
    reporter = MonthlyReporter(store, NEW_YORK, clock)
    # Already November in UTC, still October in New York.
    assert datetime.fromtimestamp(clock.now, UTC).month == 11
    assert await reporter.due_month() is None
    clock.now = datetime(2026, 11, 1, 0, 0, tzinfo=NEW_YORK).timestamp()
    assert await reporter.due_month() == Month(2026, 10)


async def test_confirmed_month_is_not_due_again(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 10, 3))
    clock = FakeClock(ts(2026, 11, 1, 1))
    reporter = MonthlyReporter(store, UTC_TZ, clock)
    month = await reporter.due_month()
    assert month == Month(2026, 10)
    await reporter.confirm(month)
    assert await store.report_posted("2026-10") is True
    assert await reporter.due_month() is None
    clock.now = ts(2026, 11, 20)
    assert await reporter.due_month() is None


async def test_restart_before_confirm_is_due_again(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 10, 3))
    clock = FakeClock(ts(2026, 11, 1, 1))
    assert await MonthlyReporter(store, UTC_TZ, clock).due_month() == Month(2026, 10)
    # A restart (new reporter, same file) without confirm: still due.
    restarted = MonthlyReporter(make_store(tmp_path), UTC_TZ, clock)
    month = await restarted.due_month()
    assert month == Month(2026, 10)
    await restarted.confirm(month)
    # After confirm, another restart finds nothing.
    assert await MonthlyReporter(make_store(tmp_path), UTC_TZ, clock).due_month() is None


async def test_missed_months_come_oldest_first_one_per_call(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 8, 10))
    await add_session(store, ts(2026, 9, 10))
    await add_session(store, ts(2026, 10, 10))
    reporter = MonthlyReporter(store, UTC_TZ, FakeClock(ts(2026, 11, 2)))
    posted = []
    while (month := await reporter.due_month()) is not None:
        posted.append(month)
        await reporter.confirm(month)
    assert posted == [Month(2026, 8), Month(2026, 9), Month(2026, 10)]


async def test_empty_month_is_skipped_but_not_marked(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 8, 10))
    await add_session(store, ts(2026, 10, 10))
    reporter = MonthlyReporter(store, UTC_TZ, FakeClock(ts(2026, 11, 2)))
    await reporter.confirm(Month(2026, 8))
    assert await reporter.due_month() == Month(2026, 10)
    assert await store.report_posted("2026-09") is False


async def test_zero_length_session_is_no_data(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 10, 10), hours=0)
    reporter = MonthlyReporter(store, UTC_TZ, FakeClock(ts(2026, 11, 2)))
    assert await reporter.due_month() is None


class CountingStore:
    """Wraps a real store and records every call the reporter makes."""

    def __init__(self, store: SqliteActivityStore) -> None:
        self.store = store
        self.calls: list[str] = []

    async def first_session_start(self) -> float | None:
        self.calls.append("first_session_start")
        return await self.store.first_session_start()

    async def posted_months(self) -> set[str]:
        self.calls.append("posted_months")
        return await self.store.posted_months()

    async def report_posted(self, month_key: str) -> bool:
        self.calls.append(f"report_posted {month_key}")
        return await self.store.report_posted(month_key)

    async def sessions_between(self, start_ts: float, end_ts: float) -> list[Session]:
        self.calls.append("sessions_between")
        return await self.store.sessions_between(start_ts, end_ts)

    async def mark_report_posted(self, month_key: str, now: float) -> None:
        self.calls.append("mark_report_posted")
        await self.store.mark_report_posted(month_key, now)


def counting_reporter(
    store: SqliteActivityStore, now: float
) -> tuple[MonthlyReporter, CountingStore]:
    counting = CountingStore(store)
    return MonthlyReporter(cast(ActivityStore, counting), UTC_TZ, FakeClock(now)), counting


async def test_never_older_than_first_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 10, 10))
    reporter, counting = counting_reporter(store, ts(2027, 1, 2))
    assert await reporter.due_month() == Month(2026, 10)
    assert counting.calls == ["first_session_start", "posted_months", "sessions_between"]


async def test_posted_months_cost_two_store_calls(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2024, 10, 10))
    month = Month(2024, 10)
    for _ in range(24):
        await store.mark_report_posted(month.key, ts(2026, 11, 1))
        month = month.next()
    assert month == Month(2026, 10)
    reporter, counting = counting_reporter(store, ts(2026, 10, 20))
    assert await reporter.due_month() is None
    assert counting.calls == ["first_session_start", "posted_months"]


async def test_only_unposted_months_are_checked_for_data(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 8, 10))
    await add_session(store, ts(2026, 10, 10))
    await store.mark_report_posted("2026-08", ts(2026, 9, 1))
    reporter, counting = counting_reporter(store, ts(2026, 11, 2))
    assert await reporter.due_month() == Month(2026, 10)
    # September (no data) and October are checked; August is skipped in memory.
    assert counting.calls == [
        "first_session_start",
        "posted_months",
        "sessions_between",
        "sessions_between",
    ]


async def test_session_spanning_month_end_counts_for_both(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await add_session(store, ts(2026, 9, 30, 23), hours=2)
    reporter = MonthlyReporter(store, UTC_TZ, FakeClock(ts(2026, 11, 2)))
    assert await reporter.due_month() == Month(2026, 9)
    await reporter.confirm(Month(2026, 9))
    assert await reporter.due_month() == Month(2026, 10)
