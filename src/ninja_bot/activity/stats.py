"""Monthly voice stats from stored sessions (pure: no discord, no IO)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ninja_bot.activity.periods import Month, month_of, split_local
from ninja_bot.activity.store import Session

# A local day counts as active with at least this much voice time that day.
ACTIVE_DAY_MIN_SECONDS = 300
# The wall of shame only names members whose last voice session started at most this
# many days before the end of the viewed month.
SHAME_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class UserStats:
    total_seconds: float
    # Local days of the month (1-based) with at least ACTIVE_DAY_MIN_SECONDS of voice.
    active_days: frozenset[int]
    longest_streak: int
    current_streak: int
    sessions_count: int
    longest_session_seconds: float
    average_session_seconds: float


@dataclass(frozen=True)
class RankedRow:
    rank: int
    user_id: int
    value: float


def clip(session: Session, start: float, end: float, now: float) -> tuple[float, float] | None:
    """The part of `session` inside [start, end); open sessions end at `now`.

    Returns None when nothing positive is left.
    """
    session_end = now if session.end_ts is None else session.end_ts
    clipped_start = max(session.start_ts, start)
    clipped_end = min(session_end, end)
    if clipped_end <= clipped_start:
        return None
    return clipped_start, clipped_end


def _seconds_per_day(start: float, end: float, tz: ZoneInfo) -> dict[int, float]:
    """Split [start, end) at local midnights; keys are local days of the month."""
    per_day: dict[int, float] = defaultdict(float)
    for piece_start, piece_end, local in split_local(start, end, tz, "day"):
        per_day[local.day] += piece_end - piece_start
    return per_day


def _longest_run(days: frozenset[int]) -> int:
    longest = 0
    for day in days:
        if day - 1 in days:
            continue
        length = 1
        while day + length in days:
            length += 1
        longest = max(longest, length)
    return longest


def _run_ending_on(days: frozenset[int], last_day: int, *, ongoing: bool) -> int:
    """Length of the run of active days ending on `last_day`.

    When `last_day` is today (`ongoing`), a quiet day so far doesn't break the
    run: it then ends on yesterday.
    """
    if ongoing and last_day not in days:
        last_day -= 1
    length = 0
    while last_day - length in days:
        length += 1
    return length


def monthly_stats(
    sessions: Iterable[Session], month: Month, tz: ZoneInfo, now: float
) -> dict[int, UserStats]:
    """Per-user stats for `month` in local time `tz`; users without time are left out.

    The current streak ends on the month's last day, or, while the month is the
    current one, on today (yesterday if today isn't active yet).
    """
    start = month.start(tz).timestamp()
    end = month.end(tz).timestamp()
    durations: dict[int, list[float]] = defaultdict(list)
    day_seconds: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for session in sessions:
        clipped = clip(session, start, end, now)
        if clipped is None:
            continue
        durations[session.user_id].append(clipped[1] - clipped[0])
        for day, seconds in _seconds_per_day(*clipped, tz).items():
            day_seconds[session.user_id][day] += seconds

    ongoing = month_of(now, tz) == month
    if ongoing:
        streak_end = datetime.fromtimestamp(now, tz).day
    else:
        streak_end = month.days()
    stats: dict[int, UserStats] = {}
    for user_id, user_durations in durations.items():
        active = frozenset(
            day
            for day, seconds in day_seconds[user_id].items()
            if seconds >= ACTIVE_DAY_MIN_SECONDS
        )
        total = sum(user_durations)
        stats[user_id] = UserStats(
            total_seconds=total,
            active_days=active,
            longest_streak=_longest_run(active),
            current_streak=_run_ending_on(active, streak_end, ongoing=ongoing),
            sessions_count=len(user_durations),
            longest_session_seconds=max(user_durations),
            average_session_seconds=total / len(user_durations),
        )
    return stats


def rank(entries: Sequence[tuple[int, float]]) -> list[RankedRow]:
    """Highest value first (user id breaks ties for stable order); ties share a rank (1, 1, 3)."""
    ordered = sorted(entries, key=lambda entry: (-entry[1], entry[0]))
    rows: list[RankedRow] = []
    for position, (user_id, value) in enumerate(ordered, start=1):
        previous = rows[-1] if rows else None
        if previous is not None and previous.value == value:
            row_rank = previous.rank
        else:
            row_rank = position
        rows.append(RankedRow(rank=row_rank, user_id=user_id, value=value))
    return rows


def shame_list(
    totals: Mapping[int, float], known_users: Iterable[int], limit: int
) -> list[tuple[int, float]]:
    """The `limit` lowest totals among `known_users` (0 when absent from `totals`), lowest first."""
    entries = [(user_id, totals.get(user_id, 0.0)) for user_id in set(known_users)]
    entries.sort(key=lambda entry: (entry[1], entry[0]))
    return entries[: max(limit, 0)]


def shame_candidates(ranges: Mapping[int, tuple[float, float]], month_end_ts: float) -> set[int]:
    """Users who may appear on the wall of shame for the month ending at `month_end_ts`.

    `ranges` maps a user to the start of their first and last voice session. A candidate
    was first seen before the month ended and last seen at most SHAME_LOOKBACK_DAYS before
    its end (later sessions count too).
    """
    cutoff = month_end_ts - SHAME_LOOKBACK_DAYS * 86400
    return {
        user_id
        for user_id, (first, last) in ranges.items()
        if first < month_end_ts and last >= cutoff
    }
