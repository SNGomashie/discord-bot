from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal

MatchOutcome = Literal["win", "loss", "tie"]


class Cs2Error(Exception):
    """Base for CS2 errors; `message` is safe to show to Discord users."""

    default_message = "Something went wrong while fetching CS2 stats."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class PlayerNotFoundError(Cs2Error):
    default_message = "Leetify doesn't know that player. Make sure they have a Leetify account."


class PrivateProfileError(Cs2Error):
    default_message = "That Leetify profile is private, so its stats can't be shown."


class RateLimitedError(Cs2Error):
    default_message = "Leetify is rate limiting us right now. Please try again later."


class ApiError(Cs2Error):
    default_message = "Leetify couldn't be reached or sent something unexpected. Try again later."


class InvalidSteamIdError(Cs2Error):
    default_message = (
        "That doesn't look like a Steam ID. "
        "Use your 17-digit SteamID64 or a `steamcommunity.com/profiles/<id>` link."
    )


class NotLinkedError(Cs2Error):
    default_message = "You haven't linked a Steam account yet. Run `/link` first."


class NoMatchesError(Cs2Error):
    default_message = "No recent matches found on Leetify for that player."


@dataclass(frozen=True)
class PlayerMatchStats:
    steam64_id: str
    name: str | None = None
    team_number: int | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    hs_kills: int | None = None
    damage: int | None = None
    rounds: int | None = None
    mvps: int | None = None
    leetify_rating: float | None = None
    accuracy_head: float | None = None
    flash_assists: int | None = None
    trade_kills: int | None = None
    multi2k: int | None = None
    multi3k: int | None = None
    multi4k: int | None = None
    multi5k: int | None = None
    rounds_survived: int | None = None
    reaction_time_ms: float | None = None
    preaim: float | None = None
    # Percentages (0-100), normalised by the parser.
    accuracy: float | None = None
    spray_accuracy: float | None = None
    shots_hit_friend: int | None = None

    @property
    def kd_ratio(self) -> float | None:
        """Kills per death; with zero deaths the kill count itself."""
        if self.kills is None or self.deaths is None:
            return None
        return self.kills / self.deaths if self.deaths else float(self.kills)

    @property
    def adr(self) -> float | None:
        """Average damage per round; None when damage or rounds is unknown or zero rounds."""
        if self.damage is None or not self.rounds:
            return None
        return self.damage / self.rounds

    @property
    def hs_percent(self) -> float | None:
        """Headshot kills as a percentage (0-100) of kills; None without kills."""
        if self.hs_kills is None or not self.kills:
            return None
        return self.hs_kills / self.kills * 100


@dataclass(frozen=True)
class TeamScore:
    team_number: int
    score: int


@dataclass(frozen=True)
class Match:
    id: str
    map_name: str | None
    finished_at: datetime.datetime | None
    team_scores: tuple[TeamScore, ...]
    players: tuple[PlayerMatchStats, ...]

    def teams(self) -> tuple[tuple[PlayerMatchStats, ...], tuple[PlayerMatchStats, ...]]:
        """The two teams ordered by team number, each sorted best player first.

        Players are sorted by Leetify rating, then kills, descending; unknown values
        sort last. Players without a team number (or on a third team) are left out.
        """
        numbers = sorted({p.team_number for p in self.players if p.team_number is not None})
        first, second = (numbers + [None, None])[:2]
        return (self._team(first), self._team(second))

    def _team(self, number: int | None) -> tuple[PlayerMatchStats, ...]:
        if number is None:
            return ()
        members = [p for p in self.players if p.team_number == number]
        return tuple(sorted(members, key=_player_sort_key))


def _player_sort_key(player: PlayerMatchStats) -> tuple[bool, float, bool, int]:
    rating, kills = player.leetify_rating, player.kills
    return (
        rating is None,
        -(rating or 0.0),
        kills is None,
        -(kills or 0),
    )


@dataclass(frozen=True)
class RecentMatch:
    id: str
    finished_at: datetime.datetime | None
    map_name: str | None
    outcome: MatchOutcome | None
    leetify_rating: float | None
    # (own team, other team) rounds.
    score: tuple[int, int] | None


@dataclass(frozen=True)
class Ranks:
    premier: int | None = None
    faceit_level: int | None = None
    leetify: float | None = None


@dataclass(frozen=True)
class Profile:
    steam64_id: str
    name: str | None
    # Fraction (0-1), as Leetify reports it.
    winrate: float | None
    total_matches: int | None
    aim: float | None
    positioning: float | None
    utility: float | None
    clutch: float | None
    opening: float | None
    ranks: Ranks | None
    # Newest first.
    recent_matches: tuple[RecentMatch, ...]
    ct_leetify: float | None = None
    t_leetify: float | None = None
