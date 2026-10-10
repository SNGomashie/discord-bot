from __future__ import annotations

import datetime
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

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


def parse_match(raw: dict[str, Any]) -> Match:
    """Build a `Match` from a Leetify `MatchDetailsResponse` (`GET /v2/matches/{id}`).

    Missing or wrongly typed values become None; entries without a steam64_id are skipped.
    Raises ApiError when the match has no usable id or no players.
    """
    match_id = _opt_id(raw.get("id"))
    if match_id is None:
        raise ApiError()
    stats = raw.get("stats")
    players = tuple(
        player
        for entry in (stats if isinstance(stats, list) else [])
        if isinstance(entry, dict) and (player := _parse_player(entry)) is not None
    )
    if not players:
        raise ApiError()
    return Match(
        id=match_id,
        map_name=_opt_str(raw.get("map_name")),
        finished_at=_opt_datetime(raw.get("finished_at")),
        team_scores=_team_scores(raw.get("team_scores")),
        players=players,
    )


def _parse_player(raw: dict[str, Any]) -> PlayerMatchStats | None:
    steam64_id = _opt_id(raw.get("steam64_id"))
    if steam64_id is None:
        return None
    return PlayerMatchStats(
        steam64_id=steam64_id,
        name=_opt_str(raw.get("name")),
        team_number=_opt_int(raw.get("initial_team_number")),
        kills=_opt_int(raw.get("total_kills")),
        deaths=_opt_int(raw.get("total_deaths")),
        assists=_opt_int(raw.get("total_assists")),
        hs_kills=_opt_int(raw.get("total_hs_kills")),
        damage=_opt_int(raw.get("total_damage")),
        rounds=_opt_int(raw.get("rounds_count")),
        mvps=_opt_int(raw.get("mvps")),
        leetify_rating=_opt_float(raw.get("leetify_rating")),
        accuracy_head=_percent(raw.get("accuracy_head")),
        flash_assists=_opt_int(raw.get("flash_assist")),
        trade_kills=_opt_int(raw.get("trade_kills_succeed")),
        multi2k=_opt_int(raw.get("multi2k")),
        multi3k=_opt_int(raw.get("multi3k")),
        multi4k=_opt_int(raw.get("multi4k")),
        multi5k=_opt_int(raw.get("multi5k")),
        rounds_survived=_opt_int(raw.get("rounds_survived")),
        reaction_time_ms=_milliseconds(raw.get("reaction_time")),
        preaim=_opt_float(raw.get("preaim")),
        accuracy=_percent(raw.get("accuracy")),
        spray_accuracy=_percent(raw.get("spray_accuracy")),
        shots_hit_friend=_opt_int(raw.get("shots_hit_friend")),
    )


def parse_profile(raw: dict[str, Any]) -> Profile:
    """Build a `Profile` from a Leetify `GET /v3/profile` response.

    Missing sections become None / empty tuples. Raises PrivateProfileError when
    `privacy_mode` is set to anything but public, ApiError without a steam64_id.
    """
    privacy = raw.get("privacy_mode")
    if privacy is not None and privacy != "public":
        raise PrivateProfileError()
    steam64_id = _opt_id(raw.get("steam64_id"))
    if steam64_id is None:
        raise ApiError()
    rating = _opt_dict(raw.get("rating"))
    recent = raw.get("recent_matches")
    return Profile(
        steam64_id=steam64_id,
        name=_opt_str(raw.get("name")),
        winrate=_opt_float(raw.get("winrate")),
        total_matches=_opt_int(raw.get("total_matches")),
        aim=_opt_float(rating.get("aim")),
        positioning=_opt_float(rating.get("positioning")),
        utility=_opt_float(rating.get("utility")),
        clutch=_opt_float(rating.get("clutch")),
        opening=_opt_float(rating.get("opening")),
        ranks=_parse_ranks(raw.get("ranks")),
        recent_matches=_newest_first(
            match
            for entry in (recent if isinstance(recent, list) else [])
            if isinstance(entry, dict) and (match := _parse_recent_match(entry)) is not None
        ),
        ct_leetify=_opt_float(rating.get("ct_leetify")),
        t_leetify=_opt_float(rating.get("t_leetify")),
    )


def _parse_ranks(value: Any) -> Ranks | None:
    if not isinstance(value, dict):
        return None
    return Ranks(
        premier=_opt_int(value.get("premier")),
        faceit_level=_opt_int(value.get("faceit")),
        leetify=_opt_float(value.get("leetify")),
    )


def _parse_recent_match(raw: dict[str, Any]) -> RecentMatch | None:
    match_id = _opt_id(raw.get("id"))
    if match_id is None:
        return None
    outcome = raw.get("outcome")
    return RecentMatch(
        id=match_id,
        finished_at=_opt_datetime(raw.get("finished_at")),
        map_name=_opt_str(raw.get("map_name")),
        outcome=outcome if outcome in ("win", "loss", "tie") else None,
        leetify_rating=_opt_float(raw.get("leetify_rating")),
        score=_score(raw.get("score")),
    )


def _score(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    own, other = _opt_int(value[0]), _opt_int(value[1])
    if own is None or other is None:
        return None
    return (own, other)


def _newest_first(matches: Iterable[RecentMatch]) -> tuple[RecentMatch, ...]:
    """Sort by `finished_at` descending; matches without a time keep their order at the end."""
    floor = datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return tuple(sorted(matches, key=lambda m: m.finished_at or floor, reverse=True))


def _team_scores(value: Any) -> tuple[TeamScore, ...]:
    if not isinstance(value, list):
        return ()
    scores: list[TeamScore] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        number, score = _opt_int(entry.get("team_number")), _opt_int(entry.get("score"))
        if number is not None and score is not None:
            scores.append(TeamScore(number, score))
    return tuple(sorted(scores, key=lambda s: s.team_number))


def _opt_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _opt_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _opt_id(value: Any) -> str | None:
    """An id as a stripped string; non-negative ints are accepted too."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    text = _opt_str(value)
    return text.strip() if text is not None else None


def _opt_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    number = _opt_float(value)
    return None if number is None else int(number)


def _opt_float(value: Any) -> float | None:
    """A finite float from an int/float, else None (bools, NaN, inf and huge ints too)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _percent(value: Any) -> float | None:
    """Match accuracies are fractions (0.5446); convert to percentages (54.46).

    Values above 1.0 are assumed to be percentages already and left alone.
    """
    number = _opt_float(value)
    if number is None or number > 1.0:
        return number
    return number * 100


def _milliseconds(value: Any) -> float | None:
    """Match reaction times are seconds (0.52); convert to ms. Values >= 10 are already ms."""
    number = _opt_float(value)
    if number is None or number >= 10:
        return number
    return number * 1000


def _opt_datetime(value: Any) -> datetime.datetime | None:
    """An ISO 8601 timestamp as an aware datetime (naive values are taken as UTC)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)
