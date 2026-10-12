from __future__ import annotations

import ast
from pathlib import Path

from ninja_bot.sessions import scoring
from ninja_bot.sessions.scoring import (
    MAX_PLAYERS,
    MAX_ROUNDS,
    MIN_PLAYERS_TO_AUTO_CLOSE,
    Fits,
    NextRound,
    NoFit,
    SlotScore,
    Vote,
    all_voted,
    evaluate_round,
    pending_players,
    score_slots,
)

Y, M, N = Vote.YES, Vote.IF_NEEDED, Vote.NO
GM = 1


def _votes(table: dict[int, list[Vote | None]], slot_ids: list[int]) -> dict[tuple[int, int], Vote]:
    return {
        (user, slot_id): vote
        for user, row in table.items()
        for slot_id, vote in zip(slot_ids, row, strict=True)
        if vote is not None
    }


def _evaluate(
    table: dict[int, list[Vote | None]],
    slot_ids: list[int],
    *,
    players: list[int] | None = None,
    min_players: int | None = None,
    round_number: int = 1,
) -> Fits | NextRound | NoFit:
    players = list(table) if players is None else players
    votes = _votes(table, slot_ids)
    scores = score_slots(slot_ids, players, votes)
    return evaluate_round(scores, players, GM, votes, min_players, round_number, MAX_ROUNDS)


def _top_ids(result: Fits | NextRound | NoFit) -> list[int]:
    assert isinstance(result, Fits)
    return [score.slot_id for score in result.top]


def test_constants() -> None:
    assert MAX_ROUNDS == 6
    assert MAX_PLAYERS == 25


def test_score_slots_counts_and_missing() -> None:
    votes = _votes({1: [Y, N], 2: [M, None], 3: [None, None]}, [10, 20])
    assert score_slots([10, 20], [1, 2, 3], votes) == [
        SlotScore(10, yes=1, if_needed=1, no=0, missing=1, attending=2),
        SlotScore(20, yes=0, if_needed=0, no=1, missing=2, attending=0),
    ]


def test_stranger_votes_ignored() -> None:
    votes = _votes({1: [Y], 99: [Y]}, [10])
    assert score_slots([10], [1], votes) == [SlotScore(10, 1, 0, 0, 0, 1)]
    result = _evaluate({1: [Y], 2: [N], 99: [Y]}, [10], players=[1, 2], min_players=2)
    assert isinstance(result, NextRound)


def test_unanimous_yes() -> None:
    result = _evaluate({1: [Y, Y], 2: [Y, Y], 3: [Y, Y]}, [10, 20])
    assert _top_ids(result) == [10, 20]


def test_ranking_and_tie_break() -> None:
    table: dict[int, list[Vote | None]] = {
        # slots: 10 (2 yes, 1 if), 20 (3 yes), 30 (1 yes, 2 if), 40 (2 yes, 1 if), 50 (2 yes, 1 if)
        1: [Y, Y, M, Y, Y],
        2: [Y, Y, Y, M, Y],
        3: [M, Y, M, Y, M],
    }
    result = _evaluate(table, [10, 20, 30, 40, 50])
    assert _top_ids(result) == [20, 10, 40]


def test_fewer_if_needed_ranks_higher() -> None:
    table: dict[int, list[Vote | None]] = {1: [Y, Y], 2: [M, Y], 3: [M, N], 4: [N, M]}
    # slot 10: 1 yes 2 if; slot 20: 2 yes 1 if -> 20 first
    assert _top_ids(_evaluate(table, [10, 20], min_players=3)) == [20, 10]
    table = {1: [Y, Y], 2: [M, M], 3: [N, M]}
    # equal yes: slot 10 has 1 if-needed, slot 20 has 2 -> 10 first
    assert _top_ids(_evaluate(table, [10, 20], min_players=2)) == [10, 20]


def test_min_players_lower_than_joined() -> None:
    table: dict[int, list[Vote | None]] = {1: [Y, N], 2: [Y, Y], 3: [N, Y], 4: [N, N]}
    assert isinstance(_evaluate(table, [10, 20]), NextRound)
    assert _top_ids(_evaluate(table, [10, 20], min_players=2)) == [10]


def test_gm_no_on_best_slot() -> None:
    table: dict[int, list[Vote | None]] = {GM: [N, M], 2: [Y, Y], 3: [Y, Y]}
    assert _top_ids(_evaluate(table, [10, 20], min_players=2)) == [20]
    table = {GM: [N], 2: [Y], 3: [Y]}
    assert isinstance(_evaluate(table, [10], min_players=2), NextRound)


def test_gm_missing_vote_does_not_fit() -> None:
    table: dict[int, list[Vote | None]] = {GM: [None], 2: [Y], 3: [Y]}
    assert isinstance(_evaluate(table, [10], min_players=2), NextRound)


def test_no_fit_with_rounds_left_and_at_last_round() -> None:
    table: dict[int, list[Vote | None]] = {GM: [Y], 2: [N]}
    assert isinstance(_evaluate(table, [10], round_number=MAX_ROUNDS - 1), NextRound)
    assert isinstance(_evaluate(table, [10], round_number=MAX_ROUNDS), NoFit)


def test_no_players() -> None:
    assert score_slots([10], [], {}) == [SlotScore(10, 0, 0, 0, 0, 0)]
    assert pending_players([], [10], {}) == []
    assert not all_voted([], [10], {})


def test_pending_players_and_all_voted() -> None:
    votes = _votes({1: [Y, None], 2: [None, None], 3: [N, N]}, [10, 20])
    assert pending_players([1, 2, 3], [10, 20], votes) == [2]
    assert not all_voted([1, 2, 3], [10, 20], votes)
    assert all_voted([1, 3], [10, 20], votes)


def test_none_of_these_is_not_pending() -> None:
    votes = _votes({1: [N, N, N]}, [10, 20, 30])
    assert pending_players([1], [10, 20, 30], votes) == []
    assert all_voted([1, 2], [10, 20, 30], {**votes, **_votes({2: [Y, N, N]}, [10, 20, 30])})


def test_lone_gm_vote_is_not_all_voted() -> None:
    assert MIN_PLAYERS_TO_AUTO_CLOSE == 2
    votes = _votes({GM: [Y]}, [10])
    assert pending_players([GM], [10], votes) == []
    assert not all_voted([GM], [10], votes)
    assert all_voted([GM, 2], [10], {**votes, **_votes({2: [N]}, [10])})


def test_votes_from_other_rounds_do_not_count() -> None:
    votes = _votes({1: [Y]}, [5])
    assert pending_players([1], [10, 20], votes) == [1]


def test_scoring_does_not_import_discord() -> None:
    tree = ast.parse(Path(scoring.__file__).read_text(encoding="utf-8"))
    modules = [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    ]
    assert not any(module.split(".")[0] == "discord" for module in modules)
