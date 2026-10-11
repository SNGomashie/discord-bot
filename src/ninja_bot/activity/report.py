"""The data behind the monthly overview and a member's profile (pure: no discord, no IO)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from ninja_bot.activity.awards import Award, SocialStats, compute_awards, social_stats
from ninja_bot.activity.periods import Month
from ninja_bot.activity.stats import RankedRow, UserStats, monthly_stats, rank, shame_list
from ninja_bot.activity.store import Session

# How many members the wall of shame names.
SHAME_LIMIT = 3


@dataclass(frozen=True)
class MonthlyReport:
    month: Month
    timezone: str  # IANA name, shown in the embed footer
    days_in_month: int
    stats: Mapping[int, UserStats]
    leaderboard: list[RankedRow]  # value = total seconds, highest first
    shame: list[tuple[int, float]]  # (user_id, seconds), lowest first, 0 included
    awards: list[Award]
    social: SocialStats


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    display_name: str
    month: Month
    timezone: str
    days_in_month: int
    stats: UserStats
    rank: int
    ranked_users: int
    awards: list[Award]


def build_monthly_report(
    sessions: Iterable[Session],
    previous_sessions: Iterable[Session],
    known_users: Iterable[int],
    month: Month,
    tz: ZoneInfo,
    now: float,
) -> MonthlyReport:
    """Everything the overview shows for `month`; open sessions end at `now`.

    `previous_sessions` cover the month before (for the climber/drop awards).
    """
    session_list = list(sessions)
    stats = monthly_stats(session_list, month, tz, now)
    previous_stats = monthly_stats(previous_sessions, month.previous(), tz, now)
    totals = {user_id: user.total_seconds for user_id, user in stats.items()}
    return MonthlyReport(
        month=month,
        timezone=tz.key,
        days_in_month=month.days(tz),
        stats=stats,
        leaderboard=rank(list(totals.items())),
        shame=shame_list(totals, set(known_users) | set(totals), SHAME_LIMIT),
        awards=compute_awards(stats, previous_stats, session_list, month, tz, now),
        social=social_stats(session_list, month, tz, now),
    )


def user_profile(report: MonthlyReport, user_id: int, display_name: str) -> UserProfile | None:
    """The member's part of `report`; None when they had no voice time that month."""
    user_stats = report.stats.get(user_id)
    if user_stats is None:
        return None
    user_rank = next(row.rank for row in report.leaderboard if row.user_id == user_id)
    return UserProfile(
        user_id=user_id,
        display_name=display_name,
        month=report.month,
        timezone=report.timezone,
        days_in_month=report.days_in_month,
        stats=user_stats,
        rank=user_rank,
        ranked_users=len(report.leaderboard),
        awards=[award for award in report.awards if award.user_id == user_id],
    )
