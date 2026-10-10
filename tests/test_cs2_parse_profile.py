import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.cs2.models import ApiError, PrivateProfileError, Ranks, parse_profile

FIXTURE = Path(__file__).parent / "fixtures" / "leetify" / "profile.json"


@pytest.fixture
def raw_profile() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def test_full_fixture(raw_profile: dict[str, Any]) -> None:
    profile = parse_profile(raw_profile)

    assert profile.steam64_id == "76561198000000000"
    assert profile.name == "alpha"
    assert profile.winrate == pytest.approx(0.54)
    assert profile.total_matches == 812
    assert profile.aim == pytest.approx(71.4)
    assert profile.positioning == pytest.approx(58.2)
    assert profile.utility == pytest.approx(49.9)
    assert profile.clutch == pytest.approx(0.12)
    assert profile.opening == pytest.approx(-0.03)
    assert profile.ct_leetify == pytest.approx(0.021)
    assert profile.t_leetify == pytest.approx(-0.008)
    assert profile.ranks == Ranks(premier=17250, faceit_level=8, leetify=2.31)

    ids = [m.id for m in profile.recent_matches]
    assert ids == [
        "8d5a1f3e-6b2c-4c11-9a7e-2f0c5b9d4e71",
        "aaaaaaaa-0000-4000-8000-000000000002",
        "aaaaaaaa-0000-4000-8000-000000000001",
    ]
    latest = profile.recent_matches[0]
    assert latest.finished_at == datetime.datetime(2026, 10, 9, 21, 14, 5, tzinfo=datetime.UTC)
    assert latest.map_name == "de_mirage"
    assert latest.outcome == "win"
    assert latest.leetify_rating == pytest.approx(0.08)
    assert latest.score == (13, 9)
    assert [m.outcome for m in profile.recent_matches] == ["win", "loss", "tie"]


def test_minimal_profile() -> None:
    profile = parse_profile({"steam64_id": "76561198000000000"})

    assert profile.steam64_id == "76561198000000000"
    assert profile.name is None
    assert profile.winrate is None
    assert profile.total_matches is None
    assert (profile.aim, profile.positioning, profile.utility) == (None, None, None)
    assert (profile.clutch, profile.opening) == (None, None)
    assert (profile.ct_leetify, profile.t_leetify) == (None, None)
    assert profile.ranks is None
    assert profile.recent_matches == ()


def test_int_steam_id_is_accepted() -> None:
    assert parse_profile({"steam64_id": 76561198000000000}).steam64_id == "76561198000000000"


@pytest.mark.parametrize("section", ["rating", "ranks", "recent_matches"])
@pytest.mark.parametrize("value", [None, "oops", 3, []])
def test_missing_or_bad_sections(raw_profile: dict[str, Any], section: str, value: object) -> None:
    raw_profile[section] = value
    profile = parse_profile(raw_profile)

    if section == "rating":
        assert profile.aim is None
        assert profile.ct_leetify is None
    elif section == "ranks":
        assert profile.ranks is None
    else:
        assert profile.recent_matches == ()


def test_null_and_wrong_types(raw_profile: dict[str, Any]) -> None:
    raw_profile.update(name=None, winrate="54%", total_matches=True)
    raw_profile["rating"]["aim"] = None
    raw_profile["rating"]["clutch"] = "high"
    raw_profile["ranks"] = {"premier": None, "faceit": "8", "leetify": float("nan")}

    profile = parse_profile(raw_profile)

    assert profile.name is None
    assert profile.winrate is None
    assert profile.total_matches is None
    assert profile.aim is None
    assert profile.clutch is None
    assert profile.positioning == pytest.approx(58.2)
    assert profile.ranks == Ranks(premier=None, faceit_level=None, leetify=None)


def test_bad_recent_match_entries(raw_profile: dict[str, Any]) -> None:
    raw_profile["recent_matches"] = [
        "not a dict",
        {"map_name": "de_dust2"},
        {
            "id": "m1",
            "finished_at": "yesterday",
            "outcome": "draw",
            "score": [13],
            "leetify_rating": None,
        },
        {"id": "m2", "finished_at": "2026-10-01T10:00:00", "score": [13, "9"]},
        {"id": "m3", "finished_at": "2026-10-02T10:00:00+00:00", "score": [16, 14]},
    ]

    matches = parse_profile(raw_profile).recent_matches

    assert [m.id for m in matches] == ["m3", "m2", "m1"]
    m3, m2, m1 = matches
    assert m3.score == (16, 14)
    assert m2.finished_at == datetime.datetime(2026, 10, 1, 10, tzinfo=datetime.UTC)
    assert m2.score is None
    assert m1.finished_at is None
    assert m1.outcome is None
    assert m1.score is None
    assert m1.leetify_rating is None
    assert m1.map_name is None


def test_undated_matches_keep_their_order() -> None:
    raw = {
        "steam64_id": "76561198000000000",
        "recent_matches": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
    }
    assert [m.id for m in parse_profile(raw).recent_matches] == ["a", "b", "c"]


@pytest.mark.parametrize("mode", ["private", "friends", "", None])
def test_privacy_mode(raw_profile: dict[str, Any], mode: str | None) -> None:
    raw_profile["privacy_mode"] = mode
    if mode is None:
        assert parse_profile(raw_profile).name == "alpha"
    else:
        with pytest.raises(PrivateProfileError):
            parse_profile(raw_profile)


def test_private_profile_without_data() -> None:
    with pytest.raises(PrivateProfileError):
        parse_profile({"privacy_mode": "private"})


@pytest.mark.parametrize("steam64_id", [None, "", "   ", -1, True, 1.5])
def test_missing_steam_id(raw_profile: dict[str, Any], steam64_id: object) -> None:
    raw_profile["steam64_id"] = steam64_id
    with pytest.raises(ApiError):
        parse_profile(raw_profile)
