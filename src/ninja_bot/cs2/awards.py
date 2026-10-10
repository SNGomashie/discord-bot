from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ninja_bot.cs2.models import Match, PlayerMatchStats

MAX_AWARDS_PER_PLAYER = 2


@dataclass(frozen=True)
class Award:
    title: str
    steam64_id: str
    name: str
    detail: str


@dataclass(frozen=True)
class _Category:
    title: str
    # The player's score for this award, or None when they don't qualify.
    value: Callable[[PlayerMatchStats], float | None]
    lower_is_better: bool
    detail: Callable[[float], str]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _at_least(count: int | None, minimum: int) -> float | None:
    return count if count is not None and count >= minimum else None


def _headshots(p: PlayerMatchStats) -> float | None:
    return p.hs_percent if p.kills is not None and p.kills >= 5 else None


def _flash_assists(p: PlayerMatchStats) -> float | None:
    return _at_least(p.flash_assists, 1)


def _trade_kills(p: PlayerMatchStats) -> float | None:
    return _at_least(p.trade_kills, 2)


def _big_rounds(p: PlayerMatchStats) -> float | None:
    counts = [c for c in (p.multi3k, p.multi4k, p.multi5k) if c is not None]
    return _at_least(sum(counts), 1) if counts else None


def _reaction_time(p: PlayerMatchStats) -> float | None:
    return p.reaction_time_ms if p.reaction_time_ms is not None and p.reaction_time_ms > 0 else None


def _survival(p: PlayerMatchStats) -> float | None:
    if p.rounds_survived is None or not p.rounds:
        return None
    return p.rounds_survived / p.rounds * 100


def _accuracy(p: PlayerMatchStats) -> float | None:
    return p.accuracy if p.kills is not None and p.kills >= 10 else None


def _friendly_hits(p: PlayerMatchStats) -> float | None:
    return _at_least(p.shots_hit_friend, 3)


# Fixed priority order: awards are handed out and listed in this order.
_CATEGORIES: tuple[_Category, ...] = (
    _Category("Headhunter", _headshots, False, lambda v: f"{v:.0f}% headshots"),
    _Category("Utility Wizard", _flash_assists, False, lambda v: _plural(int(v), "flash assist")),
    _Category("Trader", _trade_kills, False, lambda v: _plural(int(v), "trade kill")),
    _Category(
        "Ace Hunter", _big_rounds, False, lambda v: f"{_plural(int(v), 'round')} with 3+ kills"
    ),
    _Category("Lightning Reflexes", _reaction_time, True, lambda v: f"{v:.0f} ms reactions"),
    _Category("Ghost", _survival, False, lambda v: f"survived {v:.0f}% of rounds"),
    _Category("Whiffer", _accuracy, True, lambda v: f"{v:.0f}% accuracy"),
    _Category(
        "Friendly Fire Hazard",
        _friendly_hits,
        False,
        lambda v: f"{_plural(int(v), 'shot')} into teammates",
    ),
)


def _display_name(player: PlayerMatchStats) -> str:
    return player.name or player.steam64_id


def compute_awards(match: Match, limit: int = 5) -> list[Award]:
    """Rule-based awards for a match, in fixed priority order, at most `limit` of them.

    Each award goes to one player: best value, then more kills, then name. A player
    wins at most MAX_AWARDS_PER_PLAYER awards (later ones go to the next best);
    categories nobody qualifies for are skipped.
    """
    awards: list[Award] = []
    won: dict[str, int] = {}
    for category in _CATEGORIES:
        if len(awards) >= limit:
            break
        candidates = [
            (value, player)
            for player in match.players
            if won.get(player.steam64_id, 0) < MAX_AWARDS_PER_PLAYER
            and (value := category.value(player)) is not None
        ]
        if not candidates:
            continue
        sign = 1 if category.lower_is_better else -1
        value, winner = min(
            candidates,
            key=lambda c: (sign * c[0], -(c[1].kills or 0), _display_name(c[1]).casefold()),
        )
        won[winner.steam64_id] = won.get(winner.steam64_id, 0) + 1
        awards.append(
            Award(category.title, winner.steam64_id, _display_name(winner), category.detail(value))
        )
    return awards
