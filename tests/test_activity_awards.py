from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ninja_bot.activity.awards import (
    BIGGEST_CLIMBER_LABEL,
    BIGGEST_DROP_LABEL,
    EARLY_BIRD_LABEL,
    MARATHON_LABEL,
    MIN_PAIR_SECONDS,
    MOST_CONSISTENT_LABEL,
    NIGHT_OWL_LABEL,
    Award,
    AwardKey,
    TopPair,
    compute_awards,
    social_stats,
)
from ninja_bot.activity.periods import Month
from ninja_bot.activity.stats import UserStats, monthly_stats
from ninja_bot.activity.store import Session

UTC_TZ = ZoneInfo("UTC")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
OCTOBER = Month(2026, 10)
HOUR = 3600.0
LATER = datetime(2026, 12, 1, tzinfo=UTC).timestamp()


def ts(*args: int, tz: ZoneInfo = UTC_TZ) -> float:
    return datetime(*args, tzinfo=tz).timestamp()  # type: ignore[misc]


_ids = iter(range(1, 10**9))


def session(start: float, end: float | None, user_id: int = 1, channel_id: int = 10) -> Session:
    return Session(
        id=next(_ids),
        user_id=user_id,
        channel_id=channel_id,
        start_ts=start,
        end_ts=end,
        last_seen=None,
    )


def user_stats(total: float = HOUR, days: int = 1, longest: float = HOUR) -> UserStats:
    return UserStats(
        total_seconds=total,
        active_days=frozenset(range(1, days + 1)),
        longest_streak=days,
        current_streak=0,
        sessions_count=1,
        longest_session_seconds=longest,
        average_session_seconds=longest,
    )


def awards_for(
    sessions: list[Session],
    previous: dict[int, UserStats] | None = None,
    tz: ZoneInfo = UTC_TZ,
    stats: dict[int, UserStats] | None = None,
) -> list[Award]:
    current = monthly_stats(sessions, OCTOBER, tz, LATER) if stats is None else stats
    return compute_awards(current, previous or {}, sessions, OCTOBER, tz, LATER)


def of(awards: list[Award], key: AwardKey) -> list[tuple[int, float]]:
    return [(award.user_id, award.value) for award in awards if award.key == key]


# --- awards ---


def test_no_sessions_no_awards() -> None:
    assert awards_for([]) == []


def test_night_owl_winner_and_label() -> None:
    sessions = [
        session(ts(2026, 10, 5, 1), ts(2026, 10, 5, 3), user_id=1),
        session(ts(2026, 10, 5, 2), ts(2026, 10, 5, 3), user_id=2),
    ]
    awards = awards_for(sessions)
    assert of(awards, AwardKey.NIGHT_OWL) == [(1, 2 * HOUR)]
    assert next(a for a in awards if a.key == AwardKey.NIGHT_OWL).label == NIGHT_OWL_LABEL


def test_night_owl_tie_names_everyone() -> None:
    sessions = [
        session(ts(2026, 10, 5, 1), ts(2026, 10, 5, 2), user_id=2),
        session(ts(2026, 10, 6, 4), ts(2026, 10, 6, 5), user_id=1),
    ]
    assert of(awards_for(sessions), AwardKey.NIGHT_OWL) == [(1, HOUR), (2, HOUR)]


def test_night_owl_none_qualifying() -> None:
    sessions = [session(ts(2026, 10, 5, 12), ts(2026, 10, 5, 14))]
    assert of(awards_for(sessions), AwardKey.NIGHT_OWL) == []


def test_session_crossing_midnight_counts_night_part_only() -> None:
    sessions = [session(ts(2026, 10, 5, 22), ts(2026, 10, 6, 1, 30))]
    assert of(awards_for(sessions), AwardKey.NIGHT_OWL) == [(1, 1.5 * HOUR)]


def test_night_owl_uses_local_time() -> None:
    # 23:00-01:00 UTC is 01:00-03:00 in Amsterdam (CEST, +2).
    sessions = [session(ts(2026, 10, 5, 23), ts(2026, 10, 6, 1))]
    assert of(awards_for(sessions, tz=AMSTERDAM), AwardKey.NIGHT_OWL) == [(1, 2 * HOUR)]
    assert of(awards_for(sessions), AwardKey.NIGHT_OWL) == [(1, HOUR)]


def test_night_owl_on_dst_night_counts_real_seconds() -> None:
    # 2026-10-25 in Amsterdam: 03:00 CEST -> 02:00 CET, so 00:00-06:00 local lasts 7 hours.
    start = ts(2026, 10, 25, 0, tz=AMSTERDAM)
    end = ts(2026, 10, 25, 6, tz=AMSTERDAM)
    assert end - start == 7 * HOUR
    awards = awards_for([session(start, end)], tz=AMSTERDAM)
    assert of(awards, AwardKey.NIGHT_OWL) == [(1, 7 * HOUR)]


def test_early_bird_window() -> None:
    sessions = [
        session(ts(2026, 10, 5, 5), ts(2026, 10, 5, 10), user_id=1),  # 3h in 06-09
        session(ts(2026, 10, 5, 7), ts(2026, 10, 5, 8), user_id=2),
    ]
    awards = awards_for(sessions)
    assert of(awards, AwardKey.EARLY_BIRD) == [(1, 3 * HOUR)]
    assert next(a for a in awards if a.key == AwardKey.EARLY_BIRD).label == EARLY_BIRD_LABEL


def test_early_bird_tie_and_none() -> None:
    tie = [
        session(ts(2026, 10, 5, 6), ts(2026, 10, 5, 7), user_id=1),
        session(ts(2026, 10, 6, 8), ts(2026, 10, 6, 9), user_id=2),
    ]
    assert of(awards_for(tie), AwardKey.EARLY_BIRD) == [(1, HOUR), (2, HOUR)]
    late = [session(ts(2026, 10, 5, 9), ts(2026, 10, 5, 23))]
    assert of(awards_for(late), AwardKey.EARLY_BIRD) == []


def test_marathon_winner_tie_and_none() -> None:
    stats = {1: user_stats(longest=3 * HOUR), 2: user_stats(longest=HOUR)}
    awards = awards_for([], stats=stats)
    assert of(awards, AwardKey.MARATHON) == [(1, 3 * HOUR)]
    assert next(a for a in awards if a.key == AwardKey.MARATHON).label == MARATHON_LABEL
    tied = {1: user_stats(longest=HOUR), 2: user_stats(longest=HOUR)}
    assert of(awards_for([], stats=tied), AwardKey.MARATHON) == [(1, HOUR), (2, HOUR)]
    assert of(awards_for([], stats={}), AwardKey.MARATHON) == []


def test_marathon_from_sessions_is_clipped_to_month() -> None:
    sessions = [session(ts(2026, 9, 30, 22), ts(2026, 10, 1, 2))]
    assert of(awards_for(sessions), AwardKey.MARATHON) == [(1, 2 * HOUR)]


def test_most_consistent_winner_and_label() -> None:
    stats = {1: user_stats(days=3), 2: user_stats(days=5)}
    awards = awards_for([], stats=stats)
    assert of(awards, AwardKey.MOST_CONSISTENT) == [(2, 5.0)]
    assert next(a for a in awards if a.key == AwardKey.MOST_CONSISTENT).label == (
        MOST_CONSISTENT_LABEL
    )


def test_most_consistent_tie_broken_by_total() -> None:
    stats = {1: user_stats(days=4, total=2 * HOUR), 2: user_stats(days=4, total=5 * HOUR)}
    assert of(awards_for([], stats=stats), AwardKey.MOST_CONSISTENT) == [(2, 4.0)]


def test_most_consistent_full_tie_names_everyone() -> None:
    stats = {3: user_stats(days=4), 1: user_stats(days=4)}
    assert of(awards_for([], stats=stats), AwardKey.MOST_CONSISTENT) == [(1, 4.0), (3, 4.0)]


def test_most_consistent_none_without_active_days() -> None:
    stats = {1: user_stats(days=0)}
    assert of(awards_for([], stats=stats), AwardKey.MOST_CONSISTENT) == []


def test_climber_and_drop() -> None:
    stats = {1: user_stats(total=10 * HOUR), 2: user_stats(total=HOUR)}
    previous = {1: user_stats(total=2 * HOUR), 2: user_stats(total=6 * HOUR)}
    awards = awards_for([], previous=previous, stats=stats)
    assert of(awards, AwardKey.BIGGEST_CLIMBER) == [(1, 8 * HOUR)]
    assert of(awards, AwardKey.BIGGEST_DROP) == [(2, 5 * HOUR)]
    labels = {a.key: a.label for a in awards}
    assert labels[AwardKey.BIGGEST_CLIMBER] == BIGGEST_CLIMBER_LABEL
    assert labels[AwardKey.BIGGEST_DROP] == BIGGEST_DROP_LABEL


def test_climber_and_drop_count_missing_users_as_zero() -> None:
    stats = {1: user_stats(total=3 * HOUR)}  # new this month
    previous = {2: user_stats(total=4 * HOUR)}  # gone this month
    awards = awards_for([], previous=previous, stats=stats)
    assert of(awards, AwardKey.BIGGEST_CLIMBER) == [(1, 3 * HOUR)]
    assert of(awards, AwardKey.BIGGEST_DROP) == [(2, 4 * HOUR)]


def test_climber_tie_and_no_drop() -> None:
    stats = {1: user_stats(total=3 * HOUR), 2: user_stats(total=3 * HOUR)}
    previous = {1: user_stats(total=HOUR), 2: user_stats(total=HOUR)}
    awards = awards_for([], previous=previous, stats=stats)
    assert of(awards, AwardKey.BIGGEST_CLIMBER) == [(1, 2 * HOUR), (2, 2 * HOUR)]
    assert of(awards, AwardKey.BIGGEST_DROP) == []


def test_no_climber_or_drop_without_previous_month() -> None:
    stats = {1: user_stats(total=3 * HOUR)}
    awards = awards_for([], previous={}, stats=stats)
    assert of(awards, AwardKey.BIGGEST_CLIMBER) == []
    assert of(awards, AwardKey.BIGGEST_DROP) == []


def test_awards_come_in_fixed_order() -> None:
    sessions = [
        session(ts(2026, 10, 5, 1), ts(2026, 10, 5, 8), user_id=1),
        session(ts(2026, 10, 6, 12), ts(2026, 10, 6, 13), user_id=2),
    ]
    previous = {2: user_stats(total=10 * HOUR)}
    keys = [award.key for award in awards_for(sessions, previous=previous)]
    assert keys == [
        AwardKey.NIGHT_OWL,
        AwardKey.MARATHON,
        AwardKey.MOST_CONSISTENT,
        AwardKey.BIGGEST_CLIMBER,
        AwardKey.BIGGEST_DROP,
        AwardKey.EARLY_BIRD,
    ]


def test_open_session_ends_at_now() -> None:
    now = ts(2026, 10, 5, 3)
    sessions = [session(ts(2026, 10, 5, 1), None)]
    stats = monthly_stats(sessions, OCTOBER, UTC_TZ, now)
    awards = compute_awards(stats, {}, sessions, OCTOBER, UTC_TZ, now)
    assert of(awards, AwardKey.NIGHT_OWL) == [(1, 2 * HOUR)]


# --- social stats ---


def test_social_stats_empty() -> None:
    result = social_stats([], OCTOBER, UTC_TZ, LATER)
    assert result.top_pair is None
    assert result.busiest_hour is None
    assert result.busiest_weekday is None
    assert result.total_voice_seconds == 0


def test_partial_overlap_in_same_channel() -> None:
    sessions = [
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 22), user_id=2),
        session(ts(2026, 10, 5, 21), ts(2026, 10, 5, 23), user_id=1),
    ]
    result = social_stats(sessions, OCTOBER, UTC_TZ, LATER)
    assert result.top_pair == TopPair(1, 2, HOUR)
    assert result.total_voice_seconds == 4 * HOUR


def test_no_overlap_across_channels_or_time() -> None:
    sessions = [
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 22), user_id=1, channel_id=10),
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 22), user_id=2, channel_id=11),
        session(ts(2026, 10, 5, 22), ts(2026, 10, 5, 23), user_id=3, channel_id=10),
    ]
    assert social_stats(sessions, OCTOBER, UTC_TZ, LATER).top_pair is None


def test_same_user_twice_is_not_a_pair_and_counts_once() -> None:
    sessions = [
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 22), user_id=1),
        session(ts(2026, 10, 5, 21), ts(2026, 10, 5, 23), user_id=1),
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 23), user_id=2),
    ]
    result = social_stats(sessions, OCTOBER, UTC_TZ, LATER)
    assert result.top_pair == TopPair(1, 2, 3 * HOUR)


def test_short_pairs_are_ignored() -> None:
    start = ts(2026, 10, 5, 20)
    sessions = [
        session(start, start + MIN_PAIR_SECONDS - 1, user_id=1),
        session(start, start + HOUR, user_id=2),
    ]
    assert social_stats(sessions, OCTOBER, UTC_TZ, LATER).top_pair is None
    sessions[0] = session(start, start + MIN_PAIR_SECONDS, user_id=1)
    assert social_stats(sessions, OCTOBER, UTC_TZ, LATER).top_pair == TopPair(
        1, 2, MIN_PAIR_SECONDS
    )


def test_top_pair_sums_channels_and_picks_most() -> None:
    sessions = [
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 21), user_id=1, channel_id=10),
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 21), user_id=2, channel_id=10),
        session(ts(2026, 10, 6, 20), ts(2026, 10, 6, 21), user_id=1, channel_id=11),
        session(ts(2026, 10, 6, 20), ts(2026, 10, 6, 21), user_id=2, channel_id=11),
        session(ts(2026, 10, 7, 20), ts(2026, 10, 7, 21, 30), user_id=3),
        session(ts(2026, 10, 7, 20), ts(2026, 10, 7, 21, 30), user_id=4),
    ]
    assert social_stats(sessions, OCTOBER, UTC_TZ, LATER).top_pair == TopPair(1, 2, 2 * HOUR)


def test_overlap_is_clipped_to_month() -> None:
    sessions = [
        session(ts(2026, 9, 30, 20), ts(2026, 10, 1, 1), user_id=1),
        session(ts(2026, 9, 30, 20), ts(2026, 10, 1, 1), user_id=2),
    ]
    result = social_stats(sessions, OCTOBER, UTC_TZ, LATER)
    assert result.top_pair == TopPair(1, 2, HOUR)
    assert result.total_voice_seconds == 2 * HOUR


def test_busiest_hour_and_weekday() -> None:
    # 2026-10-05 is a Monday, 2026-10-07 a Wednesday.
    sessions = [
        session(ts(2026, 10, 5, 20), ts(2026, 10, 5, 21), user_id=1),
        session(ts(2026, 10, 7, 21), ts(2026, 10, 7, 22, 30), user_id=1),
        session(ts(2026, 10, 7, 21), ts(2026, 10, 7, 21, 45), user_id=2),
    ]
    result = social_stats(sessions, OCTOBER, UTC_TZ, LATER)
    assert result.busiest_hour == 21
    assert result.busiest_weekday == 2


def test_busiest_hour_uses_local_time() -> None:
    sessions = [session(ts(2026, 10, 5, 22), ts(2026, 10, 5, 23))]  # 00:00 CEST Tuesday
    result = social_stats(sessions, OCTOBER, AMSTERDAM, LATER)
    assert result.busiest_hour == 0
    assert result.busiest_weekday == 1


def test_social_stats_10k_sessions_under_a_second() -> None:
    rng = random.Random(6)
    start = ts(2026, 10, 1)
    sessions = []
    for _ in range(10_000):
        begin = start + rng.uniform(0, 30 * 24 * HOUR)
        sessions.append(
            session(
                begin,
                begin + rng.uniform(60, 3 * HOUR),
                user_id=rng.randrange(20),
                channel_id=rng.randrange(5),
            )
        )
    began = time.perf_counter()
    result = social_stats(sessions, OCTOBER, UTC_TZ, LATER)
    assert time.perf_counter() - began < 1.0
    assert result.top_pair is not None
