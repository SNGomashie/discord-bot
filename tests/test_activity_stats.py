from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ninja_bot.activity.periods import Month
from ninja_bot.activity.stats import (
    ACTIVE_DAY_MIN_SECONDS,
    RankedRow,
    clip,
    monthly_stats,
    rank,
    shame_list,
)
from ninja_bot.activity.store import Session

UTC_TZ = ZoneInfo("UTC")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
OCTOBER = Month(2026, 10)
HOUR = 3600.0
# Well after October, so October counts as a finished month.
LATER = datetime(2026, 12, 1, tzinfo=UTC).timestamp()


def ts(*args: int, tz: ZoneInfo = UTC_TZ) -> float:
    return datetime(*args, tzinfo=tz).timestamp()  # type: ignore[misc]


def session(start: float, end: float | None, user_id: int = 1, session_id: int = 1) -> Session:
    return Session(
        id=session_id,
        user_id=user_id,
        channel_id=10,
        start_ts=start,
        end_ts=end,
        last_seen=None,
    )


# --- clip ---


def test_clip_inside_window() -> None:
    s = session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 22))
    assert clip(s, ts(2026, 10, 1), ts(2026, 11, 1), LATER) == (s.start_ts, s.end_ts)


def test_clip_open_session_ends_at_now() -> None:
    now = ts(2026, 10, 5, 21)
    s = session(ts(2026, 10, 5, 20), None)
    assert clip(s, ts(2026, 10, 1), ts(2026, 11, 1), now) == (s.start_ts, now)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (ts(2026, 9, 5), ts(2026, 9, 6)),  # before the window
        (ts(2026, 11, 2), ts(2026, 11, 3)),  # after the window
        (ts(2026, 10, 5), ts(2026, 10, 5)),  # zero length
        (ts(2026, 10, 5, 2), ts(2026, 10, 5, 1)),  # negative
    ],
)
def test_clip_drops_empty(start: float, end: float) -> None:
    assert clip(session(start, end), ts(2026, 10, 1), ts(2026, 11, 1), LATER) is None


# --- monthly_stats: clipping to the month ---


def test_session_inside_month() -> None:
    stats = monthly_stats(
        [session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 22))], OCTOBER, UTC_TZ, LATER
    )
    user = stats[1]
    assert user.total_seconds == 2 * HOUR
    assert user.active_days == frozenset({5})
    assert user.sessions_count == 1
    assert user.longest_session_seconds == 2 * HOUR
    assert user.average_session_seconds == 2 * HOUR


def test_session_spanning_month_start() -> None:
    s = session(ts(2026, 9, 30, 23), ts(2026, 10, 1, 1))
    user = monthly_stats([s], OCTOBER, UTC_TZ, LATER)[1]
    assert user.total_seconds == HOUR
    assert user.active_days == frozenset({1})


def test_session_spanning_month_end() -> None:
    s = session(ts(2026, 10, 31, 23), ts(2026, 11, 1, 2))
    user = monthly_stats([s], OCTOBER, UTC_TZ, LATER)[1]
    assert user.total_seconds == HOUR
    assert user.active_days == frozenset({31})


def test_session_outside_month_is_ignored() -> None:
    s = session(ts(2026, 9, 5), ts(2026, 9, 6))
    assert monthly_stats([s], OCTOBER, UTC_TZ, LATER) == {}


def test_month_window_uses_local_time() -> None:
    # 22:30 UTC on Sep 30 is 00:30 on Oct 1 in Amsterdam (CEST, UTC+2).
    s = session(ts(2026, 9, 30, 22, 30), ts(2026, 9, 30, 23, 30))
    user = monthly_stats([s], OCTOBER, AMSTERDAM, LATER)[1]
    assert user.total_seconds == HOUR
    assert user.active_days == frozenset({1})
    assert monthly_stats([s], OCTOBER, UTC_TZ, LATER) == {}


def test_session_spanning_midnight_marks_both_days() -> None:
    s = session(ts(2026, 10, 5, 23), ts(2026, 10, 6, 1))
    user = monthly_stats([s], OCTOBER, UTC_TZ, LATER)[1]
    assert user.total_seconds == 2 * HOUR
    assert user.active_days == frozenset({5, 6})
    assert user.sessions_count == 1


def test_dst_change_counts_real_seconds() -> None:
    # Amsterdam leaves summer time on 2026-10-25: 03:00 CEST becomes 02:00 CET,
    # so local midnight to midnight is 25 real hours.
    s = session(ts(2026, 10, 25, 0, tz=AMSTERDAM), ts(2026, 10, 26, 0, tz=AMSTERDAM))
    user = monthly_stats([s], OCTOBER, AMSTERDAM, LATER)[1]
    assert user.total_seconds == 25 * HOUR
    assert user.active_days == frozenset({25})


def test_dst_change_split_at_local_midnight() -> None:
    s = session(ts(2026, 10, 25, 23, tz=AMSTERDAM), ts(2026, 10, 26, 0, 1, tz=AMSTERDAM))
    user = monthly_stats([s], OCTOBER, AMSTERDAM, LATER)[1]
    # 60 minutes on the 25th, 1 minute on the 26th: only the 25th is active.
    assert user.total_seconds == 61 * 60
    assert user.active_days == frozenset({25})


def test_open_session_ends_at_now() -> None:
    now = ts(2026, 10, 5, 21, 30)
    s = session(ts(2026, 10, 5, 20), None)
    user = monthly_stats([s], OCTOBER, UTC_TZ, now)[1]
    assert user.total_seconds == 1.5 * HOUR
    assert user.longest_session_seconds == 1.5 * HOUR


def test_session_aggregates_per_user() -> None:
    sessions = [
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 21), user_id=1, session_id=1),
        session(ts(2026, 10, 6, 20), ts(2026, 10, 6, 23), user_id=1, session_id=2),
        session(ts(2026, 10, 6, 20), ts(2026, 10, 6, 20, 30), user_id=2, session_id=3),
    ]
    stats = monthly_stats(sessions, OCTOBER, UTC_TZ, LATER)
    assert stats[1].total_seconds == 4 * HOUR
    assert stats[1].sessions_count == 2
    assert stats[1].longest_session_seconds == 3 * HOUR
    assert stats[1].average_session_seconds == 2 * HOUR
    assert stats[2].total_seconds == 0.5 * HOUR


# --- active-day threshold ---


def test_active_day_needs_five_minutes() -> None:
    short = session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 20) + ACTIVE_DAY_MIN_SECONDS - 1)
    exact = session(ts(2026, 10, 6, 20), ts(2026, 10, 6, 20) + ACTIVE_DAY_MIN_SECONDS, session_id=2)
    user = monthly_stats([short, exact], OCTOBER, UTC_TZ, LATER)[1]
    assert user.active_days == frozenset({6})
    # Time on inactive days still counts towards the total.
    assert user.total_seconds == 2 * ACTIVE_DAY_MIN_SECONDS - 1


def test_short_sessions_add_up_within_a_day() -> None:
    first = session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 20, 3))
    second = session(ts(2026, 10, 5, 21), ts(2026, 10, 5, 21, 3), session_id=2)
    user = monthly_stats([first, second], OCTOBER, UTC_TZ, LATER)[1]
    assert user.active_days == frozenset({5})


# --- streaks ---


def days_session(days: list[int], user_id: int = 1) -> list[Session]:
    return [
        session(ts(2026, 10, day, 20), ts(2026, 10, day, 21), user_id=user_id, session_id=day)
        for day in days
    ]


def test_longest_streak() -> None:
    user = monthly_stats(days_session([1, 2, 3, 10, 11, 20]), OCTOBER, UTC_TZ, LATER)[1]
    assert user.longest_streak == 3


def test_current_streak_ends_on_last_day_for_past_month() -> None:
    user = monthly_stats(days_session([1, 2, 29, 30, 31]), OCTOBER, UTC_TZ, LATER)[1]
    assert user.longest_streak == 3
    assert user.current_streak == 3


def test_current_streak_zero_when_last_day_inactive() -> None:
    user = monthly_stats(days_session([1, 2, 3, 30]), OCTOBER, UTC_TZ, LATER)[1]
    assert user.current_streak == 0


def test_current_streak_ends_today_in_current_month() -> None:
    now = ts(2026, 10, 12, 22)
    user = monthly_stats(days_session([9, 10, 11, 12]), OCTOBER, UTC_TZ, now)[1]
    assert user.current_streak == 4


def test_current_streak_zero_when_today_inactive() -> None:
    now = ts(2026, 10, 13, 22)
    user = monthly_stats(days_session([9, 10, 11, 12]), OCTOBER, UTC_TZ, now)[1]
    assert user.current_streak == 0
    assert user.longest_streak == 4


# --- rank ---


def test_rank_shares_ties() -> None:
    rows = rank([(3, 10.0), (1, 30.0), (2, 30.0), (4, 5.0)])
    assert rows == [
        RankedRow(rank=1, user_id=1, value=30.0),
        RankedRow(rank=1, user_id=2, value=30.0),
        RankedRow(rank=3, user_id=3, value=10.0),
        RankedRow(rank=4, user_id=4, value=5.0),
    ]


def test_rank_empty() -> None:
    assert rank([]) == []


# --- shame_list ---


def test_shame_list_includes_zero_hour_users() -> None:
    totals = {1: 10 * HOUR, 2: HOUR, 3: 5 * HOUR}
    assert shame_list(totals, known_users=[1, 2, 3, 4], limit=3) == [
        (4, 0.0),
        (2, HOUR),
        (3, 5 * HOUR),
    ]


def test_shame_list_does_not_invent_users() -> None:
    totals = {1: HOUR, 99: 0.5 * HOUR}
    # 99 has time but isn't a known user; nobody else is added.
    assert shame_list(totals, known_users=[1], limit=5) == [(1, HOUR)]


def test_shame_list_ties_by_user_id_and_limit() -> None:
    assert shame_list({}, known_users=[5, 2, 9], limit=2) == [(2, 0.0), (5, 0.0)]
    assert shame_list({}, known_users=[5], limit=0) == []
