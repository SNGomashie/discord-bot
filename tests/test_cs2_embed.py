import datetime
import json
from pathlib import Path

import discord
import pytest

from ninja_bot.cs2.awards import Award, compute_awards
from ninja_bot.cs2.embed import (
    AWARDS_FIELD,
    BREAKDOWN_FIELD,
    FORM_FIELD,
    YOUR_TEAM,
    build_match_embed,
    format_breakdown,
    format_player_stats,
)
from ninja_bot.cs2.formatting import form_strip
from ninja_bot.cs2.models import (
    Match,
    PlayerMatchStats,
    Profile,
    RecentMatch,
    TeamScore,
    parse_match,
    parse_profile,
)

FIXTURES = Path(__file__).parent / "fixtures" / "leetify"


@pytest.fixture
def match() -> Match:
    return parse_match(json.loads((FIXTURES / "match.json").read_text()))


@pytest.fixture
def profile() -> Profile:
    return parse_profile(json.loads((FIXTURES / "profile.json").read_text()))


def _player(index: int, team: int, **kwargs: object) -> PlayerMatchStats:
    defaults: dict[str, object] = {"name": f"player{index}", "team_number": team}
    defaults.update(kwargs)
    return PlayerMatchStats(steam64_id=f"7656119{index:010d}", **defaults)  # type: ignore[arg-type]


def _minimal_match(*scores: TeamScore, map_name: str | None = None) -> Match:
    return Match(
        id="m1",
        map_name=map_name,
        finished_at=None,
        team_scores=scores,
        players=(_player(1, 2), _player(2, 3)),
    )


def _fields(embed: discord.Embed) -> dict[str, str]:
    return {f.name or "": f.value or "" for f in embed.fields}


def _assert_no_empty_fields(embed: discord.Embed) -> None:
    for field in embed.fields:
        assert field.name and field.name.strip()
        assert field.value and field.value.strip()
        assert "None" not in field.value


def test_full_fixture(match: Match, profile: Profile) -> None:
    focus = next(p for p in match.players if p.steam64_id == profile.steam64_id)
    awards = compute_awards(match)
    embed = build_match_embed(match, awards, focus.steam64_id, profile)

    scores = {s.team_number: s.score for s in match.team_scores}
    assert embed.title == f"{match.map_name} - {scores[2]}:{scores[3]}"
    assert embed.timestamp == match.finished_at
    own = scores[focus.team_number or 0]
    other = next(v for k, v in scores.items() if k != focus.team_number)
    expected = discord.Colour.green() if own > other else discord.Colour.red()
    assert embed.colour == expected

    names = [f.name for f in embed.fields]
    assert YOUR_TEAM in names[:2]
    assert {"Team A", "Team B"} & set(names[:2])
    assert all(not f.inline for f in embed.fields[:2])
    fields = _fields(embed)
    assert fields[AWARDS_FIELD].count("\n") == len(awards) - 1
    assert focus.name in fields
    assert "K/D/A:" in fields[focus.name or ""]
    assert "Aim: 71" in fields[BREAKDOWN_FIELD]
    assert fields[FORM_FIELD] == form_strip(profile.recent_matches)
    _assert_no_empty_fields(embed)


def test_minimal_match() -> None:
    embed = build_match_embed(_minimal_match(), [])
    assert embed.title == "Unknown map"
    assert embed.timestamp is None
    assert embed.colour == discord.Colour.light_grey()
    assert [f.name for f in embed.fields] == ["Team A", "Team B"]
    _assert_no_empty_fields(embed)


def test_focus_without_known_stats_or_profile_data_is_omitted() -> None:
    empty_profile = Profile(
        steam64_id="76561190000000001",
        name=None,
        winrate=None,
        total_matches=None,
        aim=None,
        positioning=None,
        utility=None,
        clutch=None,
        opening=None,
        ranks=None,
        recent_matches=(),
    )
    embed = build_match_embed(_minimal_match(), [], "76561190000000001", empty_profile)
    assert [f.name for f in embed.fields] == [YOUR_TEAM, "Team B"]


def test_focus_player_missing_from_match(profile: Profile) -> None:
    match = _minimal_match(TeamScore(2, 13), TeamScore(3, 5), map_name="de_nuke")
    embed = build_match_embed(match, [], "76561199999999999", profile)
    assert embed.title == "de_nuke - 13:5"
    assert embed.colour == discord.Colour.light_grey()
    assert [f.name for f in embed.fields] == ["Team A", "Team B"]


@pytest.mark.parametrize(
    ("scores", "colour"),
    [
        ((13, 7), discord.Colour.green()),
        ((7, 13), discord.Colour.red()),
        ((12, 12), discord.Colour.light_grey()),
    ],
)
def test_outcome_colours(scores: tuple[int, int], colour: discord.Colour) -> None:
    match = _minimal_match(TeamScore(2, scores[0]), TeamScore(3, scores[1]))
    embed = build_match_embed(match, [], "76561190000000001")
    assert embed.colour == colour
    # The other side sees the opposite result.
    other = build_match_embed(match, [], "76561190000000002")
    if scores[0] != scores[1]:
        assert other.colour != colour


def test_awards_field() -> None:
    awards = [
        Award("Headhunter", "1", "alice", "60% headshots"),
        Award("Trader", "2", "bob", "3 trade kills"),
    ]
    embed = build_match_embed(_minimal_match(), awards)
    assert _fields(embed)[AWARDS_FIELD] == (
        "**Headhunter**: alice (60% headshots)\n**Trader**: bob (3 trade kills)"
    )


def test_format_player_stats() -> None:
    player = _player(
        1,
        2,
        kills=20,
        deaths=10,
        assists=None,
        hs_kills=10,
        damage=2000,
        rounds=25,
        leetify_rating=0.034,
        preaim=7.25,
        reaction_time_ms=512.4,
        multi2k=3,
        multi3k=0,
        multi4k=1,
    )
    assert format_player_stats(player) == (
        "K/D/A: 20/10/-\nADR: 80\nHS: 50%\nRating: +0.03\n"
        "Preaim: 7.2°\nReaction time: 512 ms\nMulti-kills: 3× 2k · 1× 4k"
    )
    assert format_player_stats(_player(1, 2)) == ""


def test_format_breakdown(profile: Profile) -> None:
    assert format_breakdown(profile) == (
        "Aim: 71\nPositioning: 58\nUtility: 50\nClutch: +0.12\nOpening: -0.03"
    )


def test_limits_full_match_with_long_names(profile: Profile) -> None:
    long_name = "W" * 32
    players = tuple(
        _player(
            i,
            2 if i < 5 else 3,
            name=f"{long_name[:-2]}{i:02d}",
            kills=99,
            deaths=99,
            assists=99,
            hs_kills=99,
            damage=99999,
            rounds=30,
            mvps=i,
            leetify_rating=-0.123,
            flash_assists=9,
            trade_kills=9,
            multi2k=9,
            multi3k=9,
            multi4k=9,
            multi5k=9,
            rounds_survived=29,
            reaction_time_ms=999.0,
            preaim=99.9,
            accuracy=12.3,
            shots_hit_friend=9,
        )
        for i in range(10)
    )
    recent = tuple(
        RecentMatch(
            id=str(i),
            finished_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            map_name="de_ancient",
            outcome="win",
            leetify_rating=0.1,
            score=(13, 11),
        )
        for i in range(20)
    )
    match = Match(
        id="m",
        map_name="de_" + "x" * 300,
        finished_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        team_scores=(TeamScore(2, 16), TeamScore(3, 14)),
        players=players,
    )
    awards = compute_awards(match, limit=25)
    long_profile = Profile(
        steam64_id=players[0].steam64_id,
        name=long_name,
        winrate=0.5,
        total_matches=1000,
        aim=99.9,
        positioning=99.9,
        utility=99.9,
        clutch=-0.99,
        opening=-0.99,
        ranks=profile.ranks,
        recent_matches=recent,
    )

    embed = build_match_embed(match, awards, players[0].steam64_id, long_profile)

    assert len(embed.fields) == 6
    assert len(embed.fields) <= 25
    assert len(embed.title or "") <= 256
    for field in embed.fields:
        assert len(field.name or "") <= 256
        assert len(field.value or "") <= 1024
    assert len(embed) <= 6000
    _assert_no_empty_fields(embed)
