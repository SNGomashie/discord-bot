"""The data behind the monthly overview and a member's profile (pure: no discord, no IO)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo

from ninja_bot.activity.awards import Award, SocialStats, compute_awards, social_stats
from ninja_bot.activity.periods import Month
from ninja_bot.activity.pokemon_stats import (
    PokemonStats,
    PokemonUserStats,
    impatient_awards,
    pokemon_stats,
)
from ninja_bot.activity.stats import (
    RankedRow,
    UserStats,
    monthly_stats,
    rank,
    shame_candidates,
    shame_list,
)
from ninja_bot.activity.store import PokemonEvent, Session

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
    awards: list[Award]  # voice awards, then Impatient
    social: SocialStats
    pokemon: PokemonStats = field(default_factory=PokemonStats)
    # Every wall of shame candidate, lowest first; `shame` is its head until
    # `without_departed` drops members who left the server.
    shame_pool: list[tuple[int, float]] = field(default_factory=list)


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
    pokemon: PokemonUserStats | None = None  # None when the month has no Pokémon data


def build_monthly_report(
    sessions: Iterable[Session],
    previous_sessions: Iterable[Session],
    voice_user_ranges: Mapping[int, tuple[float, float]],
    month: Month,
    tz: ZoneInfo,
    now: float,
    pokemon_events: Iterable[PokemonEvent] = (),
) -> MonthlyReport:
    """Everything the overview shows for `month`; open sessions end at `now`.

    `previous_sessions` cover the month before (for the climber/drop awards);
    `voice_user_ranges` (first and last session start per user) pick the wall of shame
    candidates (see `shame_candidates`); `pokemon_events` outside `month` are ignored.
    """
    session_list = list(sessions)
    pokemon = pokemon_stats(pokemon_events, month, tz)
    stats = monthly_stats(session_list, month, tz, now)
    previous_stats = monthly_stats(previous_sessions, month.previous(), tz, now)
    totals = {user_id: user.total_seconds for user_id, user in stats.items()}
    candidates = shame_candidates(voice_user_ranges, month.end(tz).timestamp()) | set(totals)
    pool = shame_list(totals, candidates, len(candidates))
    return MonthlyReport(
        month=month,
        timezone=tz.key,
        days_in_month=month.days(),
        stats=stats,
        leaderboard=rank(list(totals.items())),
        shame=pool[:SHAME_LIMIT],
        awards=compute_awards(stats, previous_stats, session_list, month, tz, now)
        + impatient_awards(pokemon),
        social=social_stats(session_list, month, tz, now),
        pokemon=pokemon,
        shame_pool=pool,
    )


def drop_departed(
    pool: Iterable[tuple[int, float]], departed: Set[int], limit: int
) -> list[tuple[int, float]]:
    """The first `limit` entries of `pool` whose user is not in `departed` (order kept)."""
    return [entry for entry in pool if entry[0] not in departed][: max(limit, 0)]


def without_departed(report: MonthlyReport, departed: Set[int]) -> MonthlyReport:
    """`report` with members who left the server off the wall of shame (refilled from the pool)."""
    return replace(report, shame=drop_departed(report.shame_pool, departed, SHAME_LIMIT))


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
        pokemon=(
            report.pokemon.users.get(user_id, PokemonUserStats())
            if report.pokemon.has_data
            else None
        ),
    )
