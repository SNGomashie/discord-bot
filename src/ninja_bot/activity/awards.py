"""Monthly fun awards and server-wide social stats (pure: no discord, no IO)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from ninja_bot.activity.periods import Month, split_local
from ninja_bot.activity.stats import UserStats, clip
from ninja_bot.activity.store import Session

NIGHT_OWL_LABEL = "Night owl"
MARATHON_LABEL = "Marathon"
MOST_CONSISTENT_LABEL = "Most consistent"
BIGGEST_CLIMBER_LABEL = "Biggest climber"
BIGGEST_DROP_LABEL = "Biggest drop"
EARLY_BIRD_LABEL = "Early bird"

# Local hours (start inclusive, end exclusive) for the time-of-day awards.
NIGHT_OWL_HOURS = range(0, 6)
EARLY_BIRD_HOURS = range(6, 9)

# Pairs with less shared time than this in one channel don't count for `top_pair`.
MIN_PAIR_SECONDS = 600


class AwardKey(StrEnum):
    NIGHT_OWL = "night_owl"
    MARATHON = "marathon"
    MOST_CONSISTENT = "most_consistent"
    BIGGEST_CLIMBER = "biggest_climber"
    BIGGEST_DROP = "biggest_drop"
    EARLY_BIRD = "early_bird"
    IMPATIENT = "impatient"  # Toasty cooldown hits (activity/pokemon_stats.py)


@dataclass(frozen=True)
class Award:
    """One award winner; a tie gives one `Award` per tied user with the same key.

    `value` is seconds, except MOST_CONSISTENT (active days) and IMPATIENT (cooldown hits);
    BIGGEST_DROP is positive.
    """

    key: AwardKey
    user_id: int
    value: float
    label: str


@dataclass(frozen=True)
class TopPair:
    first: int  # lower user id
    second: int
    seconds: float


@dataclass(frozen=True)
class SocialStats:
    top_pair: TopPair | None
    busiest_hour: int | None  # local hour 0-23
    busiest_weekday: int | None  # 0 = Monday
    total_voice_seconds: float


def _clipped(
    sessions: Iterable[Session], month: Month, tz: ZoneInfo, now: float
) -> Iterator[tuple[Session, float, float]]:
    start = month.start(tz).timestamp()
    end = month.end(tz).timestamp()
    for session in sessions:
        clipped = clip(session, start, end, now)
        if clipped is not None:
            yield session, clipped[0], clipped[1]


def _hour_pieces(start: float, end: float, tz: ZoneInfo) -> Iterator[tuple[datetime, float]]:
    """Split [start, end) at local hour boundaries: (local start of piece, real seconds)."""
    for piece_start, piece_end, local in split_local(start, end, tz, "hour"):
        yield local, piece_end - piece_start


def _winners(
    key: AwardKey,
    label: str,
    values: Mapping[int, float],
    tiebreak: Mapping[int, float] | None = None,
) -> list[Award]:
    """Everyone sharing the highest positive value (then highest tiebreak), lowest id first."""
    best = max(values.values(), default=0.0)
    if best <= 0:
        return []
    tied = [user_id for user_id, value in values.items() if value == best]
    if tiebreak is not None:
        best_tiebreak = max(tiebreak.get(user_id, 0.0) for user_id in tied)
        tied = [user_id for user_id in tied if tiebreak.get(user_id, 0.0) == best_tiebreak]
    return [Award(key, user_id, best, label) for user_id in sorted(tied)]


def _seconds_in_hours(
    sessions: Iterable[Session], month: Month, tz: ZoneInfo, now: float, hours: range
) -> dict[int, float]:
    per_user: dict[int, float] = defaultdict(float)
    for session, start, end in _clipped(sessions, month, tz, now):
        for local, seconds in _hour_pieces(start, end, tz):
            if local.hour in hours:
                per_user[session.user_id] += seconds
    return per_user


def compute_awards(
    stats: Mapping[int, UserStats],
    previous_stats: Mapping[int, UserStats],
    sessions: Iterable[Session],
    month: Month,
    tz: ZoneInfo,
    now: float,
) -> list[Award]:
    """Awards for `month` in a fixed order; open sessions end at `now` (as in `monthly_stats`).

    Climber/drop compare total time with `previous_stats` and only appear once `month`
    has ended (`now` is past its end) and that previous month had data; users missing
    from either month count as 0 there.
    """
    session_list = list(sessions)
    totals = {user_id: user.total_seconds for user_id, user in stats.items()}
    awards = _winners(
        AwardKey.NIGHT_OWL,
        NIGHT_OWL_LABEL,
        _seconds_in_hours(session_list, month, tz, now, NIGHT_OWL_HOURS),
    )
    awards.extend(
        _winners(
            AwardKey.MARATHON,
            MARATHON_LABEL,
            {user_id: user.longest_session_seconds for user_id, user in stats.items()},
        )
    )
    awards.extend(
        _winners(
            AwardKey.MOST_CONSISTENT,
            MOST_CONSISTENT_LABEL,
            {user_id: float(len(user.active_days)) for user_id, user in stats.items()},
            tiebreak=totals,
        )
    )
    if previous_stats and now >= month.end(tz).timestamp():
        users = set(stats) | set(previous_stats)
        change = {
            user_id: totals.get(user_id, 0.0)
            - (previous_stats[user_id].total_seconds if user_id in previous_stats else 0.0)
            for user_id in users
        }
        awards.extend(_winners(AwardKey.BIGGEST_CLIMBER, BIGGEST_CLIMBER_LABEL, change))
        awards.extend(
            _winners(
                AwardKey.BIGGEST_DROP,
                BIGGEST_DROP_LABEL,
                {user_id: -delta for user_id, delta in change.items()},
            )
        )
    awards.extend(
        _winners(
            AwardKey.EARLY_BIRD,
            EARLY_BIRD_LABEL,
            _seconds_in_hours(session_list, month, tz, now, EARLY_BIRD_HOURS),
        )
    )
    return awards


def _channel_overlaps(
    intervals: list[tuple[float, float, int]], pairs: dict[tuple[int, int], float]
) -> None:
    """Sweep one channel's (start, end, user) intervals, adding shared seconds per user pair."""
    events: list[tuple[float, int, int]] = []
    for start, end, user_id in intervals:
        events.append((start, 1, user_id))
        events.append((end, -1, user_id))
    events.sort()  # at equal times, leaves (-1) come before joins
    present: dict[int, int] = {}  # user -> open intervals (same user twice counts once)
    previous_time: float | None = None
    for time, delta, user_id in events:
        if previous_time is not None and time > previous_time and len(present) > 1:
            span = time - previous_time
            users = sorted(present)
            for index, first in enumerate(users):
                for second in users[index + 1 :]:
                    pairs[(first, second)] += span
        previous_time = time
        count = present.get(user_id, 0) + delta
        if count > 0:
            present[user_id] = count
        else:
            present.pop(user_id, None)


def social_stats(
    sessions: Iterable[Session], month: Month, tz: ZoneInfo, now: float
) -> SocialStats:
    """Server-wide stats for `month`; open sessions end at `now`."""
    by_channel: dict[int, list[tuple[float, float, int]]] = defaultdict(list)
    hours: dict[int, float] = defaultdict(float)
    weekdays: dict[int, float] = defaultdict(float)
    total = 0.0
    for session, start, end in _clipped(sessions, month, tz, now):
        by_channel[session.channel_id].append((start, end, session.user_id))
        total += end - start
        for local, seconds in _hour_pieces(start, end, tz):
            hours[local.hour] += seconds
            weekdays[local.weekday()] += seconds

    pairs: dict[tuple[int, int], float] = defaultdict(float)
    for intervals in by_channel.values():
        _channel_overlaps(intervals, pairs)
    eligible = [(pair, seconds) for pair, seconds in pairs.items() if seconds >= MIN_PAIR_SECONDS]
    top_pair = None
    if eligible:
        (first, second), seconds = min(eligible, key=lambda item: (-item[1], item[0]))
        top_pair = TopPair(first, second, seconds)

    return SocialStats(
        top_pair=top_pair,
        busiest_hour=_busiest(hours),
        busiest_weekday=_busiest(weekdays),
        total_voice_seconds=total,
    )


def _busiest(seconds_by_key: Mapping[int, float]) -> int | None:
    """Key with the most seconds (lowest key on a tie); None without data."""
    if not seconds_by_key:
        return None
    return min(seconds_by_key, key=lambda key: (-seconds_by_key[key], key))
