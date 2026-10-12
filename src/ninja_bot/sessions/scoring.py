"""Session poll vote scoring and round evaluation (no discord import)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

# Rounds before a poll gives up without a date.
MAX_ROUNDS = 6
# Most joined players per poll.
MAX_PLAYERS = 25
# Most fitting slots shown when a round finds a date.
MAX_TOP_SLOTS = 3
# Fewest joined players (the GM and one more) before "everyone voted" closes a round.
MIN_PLAYERS_TO_AUTO_CLOSE = 2


class Vote(StrEnum):
    YES = "yes"
    IF_NEEDED = "if_needed"
    NO = "no"


# (user_id, slot_id) -> that user's vote on that slot.
Votes = Mapping[tuple[int, int], Vote]


@dataclass(frozen=True)
class SlotScore:
    slot_id: int
    yes: int
    if_needed: int
    no: int
    missing: int
    attending: int


@dataclass(frozen=True)
class Fits:
    """At least one slot fits; `top` holds up to `MAX_TOP_SLOTS`, best first."""

    top: tuple[SlotScore, ...]


@dataclass(frozen=True)
class NextRound:
    """Nothing fits and rounds are left."""


@dataclass(frozen=True)
class NoFit:
    """Nothing fits in the last round."""


RoundResult = Fits | NextRound | NoFit


def score_slots(slot_ids: Iterable[int], players: Sequence[int], votes: Votes) -> list[SlotScore]:
    """Counts the joined players' votes per slot; votes from non-players are ignored."""
    scores = []
    for slot_id in slot_ids:
        counts = dict.fromkeys(Vote, 0)
        missing = 0
        for player in players:
            vote = votes.get((player, slot_id))
            if vote is None:
                missing += 1
            else:
                counts[vote] += 1
        yes, if_needed = counts[Vote.YES], counts[Vote.IF_NEEDED]
        scores.append(SlotScore(slot_id, yes, if_needed, counts[Vote.NO], missing, yes + if_needed))
    return scores


def evaluate_round(
    scores: Sequence[SlotScore],
    players: Sequence[int],
    gm_id: int,
    votes: Votes,
    min_players: int | None,
    round_number: int,
    max_rounds: int,
) -> RoundResult:
    """A slot fits when enough players attend and the GM can make it."""
    required = min_players or len(players)
    fitting = [
        score
        for score in scores
        if score.attending >= required
        and votes.get((gm_id, score.slot_id)) in (Vote.YES, Vote.IF_NEEDED)
    ]
    if fitting:
        ranked = sorted(fitting, key=lambda s: (-s.yes, s.if_needed, s.slot_id))
        return Fits(tuple(ranked[:MAX_TOP_SLOTS]))
    if round_number < max_rounds:
        return NextRound()
    return NoFit()


def pending_players(
    players: Sequence[int], round_slot_ids: Sequence[int], votes: Votes
) -> list[int]:
    """Joined players without a vote on any slot of the round."""
    return [
        player
        for player in players
        if not any((player, slot_id) in votes for slot_id in round_slot_ids)
    ]


def all_voted(players: Sequence[int], round_slot_ids: Sequence[int], votes: Votes) -> bool:
    """Every joined player voted, with at least `MIN_PLAYERS_TO_AUTO_CLOSE` joined.

    The GM is auto-joined, so without the minimum the GM's own vote would close
    (and burn) the round before anyone else could join.
    """
    return len(players) >= MIN_PLAYERS_TO_AUTO_CLOSE and not pending_players(
        players, round_slot_ids, votes
    )
