import copy
import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.cs2.models import ApiError, TeamScore, parse_match

FIXTURE = Path(__file__).parent / "fixtures" / "leetify" / "match.json"


@pytest.fixture
def raw_match() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def _minimal(**player: Any) -> dict[str, Any]:
    return {"id": "m1", "stats": [{"steam64_id": "76561198000000001", **player}]}


def test_full_fixture(raw_match: dict[str, Any]) -> None:
    match = parse_match(raw_match)

    assert match.id == "8d5a1f3e-6b2c-4c11-9a7e-2f0c5b9d4e71"
    assert match.map_name == "de_mirage"
    assert match.finished_at == datetime.datetime(2026, 10, 9, 21, 14, 5, tzinfo=datetime.UTC)
    assert match.team_scores == (TeamScore(2, 13), TeamScore(3, 9))
    assert len(match.players) == 10
    team_a, team_b = match.teams()
    assert len(team_a) == len(team_b) == 5

    alpha = match.players[0]
    assert alpha.steam64_id == "76561198000000000"
    assert alpha.name == "alpha"
    assert alpha.team_number == 2
    assert (alpha.kills, alpha.deaths, alpha.assists) == (24, 10, 3)
    assert alpha.hs_kills == 12
    assert alpha.damage == 2400
    assert alpha.rounds == 22
    assert alpha.mvps == 5
    assert alpha.leetify_rating == pytest.approx(0.08)
    assert alpha.accuracy == pytest.approx(54.46)
    assert alpha.accuracy_head == pytest.approx(31.0)
    assert alpha.spray_accuracy == pytest.approx(41.0)
    assert alpha.preaim == pytest.approx(6.5)
    assert alpha.reaction_time_ms == pytest.approx(480.0)
    assert alpha.flash_assists == 0
    assert alpha.trade_kills == 0
    assert (alpha.multi2k, alpha.multi3k, alpha.multi4k, alpha.multi5k) == (4, 2, 1, 0)
    assert alpha.rounds_survived == 12
    assert alpha.shots_hit_friend == 0
    assert alpha.adr == pytest.approx(2400 / 22)


def test_minimal_match() -> None:
    match = parse_match(_minimal())

    assert match.id == "m1"
    assert match.map_name is None
    assert match.finished_at is None
    assert match.team_scores == ()
    (player,) = match.players
    assert player.steam64_id == "76561198000000001"
    assert player.name is None
    assert player.kills is None
    assert player.accuracy is None
    assert player.reaction_time_ms is None


def test_null_fields_become_none(raw_match: dict[str, Any]) -> None:
    raw = copy.deepcopy(raw_match)
    raw["map_name"] = None
    raw["finished_at"] = None
    raw["team_scores"] = None
    for key in list(raw["stats"][0]):
        if key != "steam64_id":
            raw["stats"][0][key] = None

    match = parse_match(raw)

    assert match.map_name is None
    assert match.finished_at is None
    assert match.team_scores == ()
    player = match.players[0]
    assert player.steam64_id == "76561198000000000"
    assert player.name is None
    assert player.team_number is None
    assert player.kills is None
    assert player.leetify_rating is None
    assert player.accuracy is None


@pytest.mark.parametrize(
    "value",
    ["12", True, [1], {"a": 1}, float("nan"), float("inf")],
)
def test_wrong_types_become_none(value: Any) -> None:
    raw = _minimal(
        total_kills=value,
        leetify_rating=value,
        accuracy=value,
        reaction_time=value,
        initial_team_number=value,
        name=5,
    )
    raw["map_name"] = 42
    raw["finished_at"] = "not a date"
    raw["team_scores"] = [{"team_number": "2", "score": 13}, "junk", {"team_number": 3, "score": 9}]

    match = parse_match(raw)

    player = match.players[0]
    assert player.kills is None
    assert player.leetify_rating is None
    assert player.accuracy is None
    assert player.reaction_time_ms is None
    assert player.team_number is None
    assert player.name is None
    assert match.map_name is None
    assert match.finished_at is None
    assert match.team_scores == (TeamScore(3, 9),)


def test_float_counts_are_truncated() -> None:
    assert parse_match(_minimal(total_kills=12.0)).players[0].kills == 12


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5446, 54.46), (0.0, 0.0), (1.0, 100.0), (54.46, 54.46), (100, 100.0)],
)
def test_accuracy_normalised_to_percent(value: float, expected: float) -> None:
    player = parse_match(
        _minimal(accuracy=value, accuracy_head=value, spray_accuracy=value)
    ).players[0]

    assert player.accuracy == pytest.approx(expected)
    assert player.accuracy_head == pytest.approx(expected)
    assert player.spray_accuracy == pytest.approx(expected)


@pytest.mark.parametrize(("value", "expected"), [(0.52, 520.0), (612, 612.0)])
def test_reaction_time_in_milliseconds(value: float, expected: float) -> None:
    assert parse_match(_minimal(reaction_time=value)).players[0].reaction_time_ms == pytest.approx(
        expected
    )


def test_naive_timestamp_is_utc() -> None:
    raw = _minimal()
    raw["finished_at"] = "2026-10-09T21:14:05"

    assert parse_match(raw).finished_at == datetime.datetime(
        2026, 10, 9, 21, 14, 5, tzinfo=datetime.UTC
    )


def test_numeric_ids_are_strings() -> None:
    raw = {"id": 123, "stats": [{"steam64_id": 76561198000000001}]}

    match = parse_match(raw)

    assert match.id == "123"
    assert match.players[0].steam64_id == "76561198000000001"


def test_player_without_steam_id_is_skipped(raw_match: dict[str, Any]) -> None:
    raw = copy.deepcopy(raw_match)
    del raw["stats"][0]["steam64_id"]
    raw["stats"][1]["steam64_id"] = None
    raw["stats"].append("junk")

    match = parse_match(raw)

    assert len(match.players) == 8
    assert all(p.steam64_id for p in match.players)


@pytest.mark.parametrize(
    "raw",
    [
        {"stats": [{"steam64_id": "76561198000000001"}]},
        {"id": None, "stats": [{"steam64_id": "76561198000000001"}]},
        {"id": "   ", "stats": [{"steam64_id": "76561198000000001"}]},
        {"id": True, "stats": [{"steam64_id": "76561198000000001"}]},
        {"id": "m1"},
        {"id": "m1", "stats": []},
        {"id": "m1", "stats": None},
        {"id": "m1", "stats": [{"name": "no id"}]},
    ],
)
def test_unusable_match_raises_api_error(raw: dict[str, Any]) -> None:
    with pytest.raises(ApiError):
        parse_match(raw)
