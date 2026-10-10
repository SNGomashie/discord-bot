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
    MATCHES_FIELD,
    PROFILE_COLOUR,
    RANKS_FIELD,
    RATINGS_FIELD,
    WINRATE_FIELD,
    YOUR_TEAM,
    build_match_embed,
    build_profile_embed,
    format_breakdown,
    format_player_stats,
    format_ranks,
    format_rating_bars,
)
from ninja_bot.cs2.formatting import (
    BAR_EMPTY,
    BAR_FILLED,
    ESCAPED_NAME_LENGTH,
    escape_name,
    form_strip,
    rating_bar,
)
from ninja_bot.cs2.models import (
    Match,
    PlayerMatchStats,
    Profile,
    Ranks,
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


def test_hostile_names_are_escaped_outside_code_blocks() -> None:
    hostile = "[free nitro](https://evil.example)"
    match = Match(
        id="m1",
        map_name="de_dust2",
        finished_at=None,
        team_scores=(TeamScore(2, 13), TeamScore(3, 7)),
        players=(_player(1, 2, name=hostile, kills=20), _player(2, 3)),
    )
    awards = [Award("Headhunter", match.players[0].steam64_id, hostile, "60% headshots")]
    embed = build_match_embed(match, awards, focus_steam_id=match.players[0].steam64_id)
    fields = _fields(embed)
    assert "[free nitro](" not in fields[AWARDS_FIELD]
    assert r"\[free nitro\]\(" in fields[AWARDS_FIELD]
    assert all(not name.startswith("[") for name in fields)
    assert escape_name(hostile) in fields


def test_profile_embed_title_is_escaped() -> None:
    embed = build_profile_embed(_minimal_profile(name="**[x](https://evil.example)**"))
    assert embed.title == escape_name("**[x](https://evil.example)**")
    assert "](" not in (embed.title or "")


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


def _minimal_profile(**kwargs: object) -> Profile:
    defaults: dict[str, object] = {
        "steam64_id": "76561198000000009",
        "name": None,
        "winrate": None,
        "total_matches": None,
        "aim": None,
        "positioning": None,
        "utility": None,
        "clutch": None,
        "opening": None,
        "ranks": None,
        "recent_matches": (),
    }
    defaults.update(kwargs)
    return Profile(**defaults)  # type: ignore[arg-type]


def test_profile_embed_full_fixture(profile: Profile) -> None:
    embed = build_profile_embed(profile)
    assert embed.title == "alpha"
    assert embed.colour == PROFILE_COLOUR
    fields = _fields(embed)
    assert list(fields) == [RANKS_FIELD, WINRATE_FIELD, MATCHES_FIELD, RATINGS_FIELD, FORM_FIELD]
    assert fields[RANKS_FIELD] == "Premier: 17,250\nFACEIT: level 8\nLeetify: 2.31"
    assert fields[WINRATE_FIELD] == "54%"
    assert fields[MATCHES_FIELD] == "812"
    assert fields[FORM_FIELD] == form_strip(profile.recent_matches)
    ratings = fields[RATINGS_FIELD]
    assert ratings.startswith("```\n") and ratings.endswith("\n```")
    for label in ("Aim", "Positioning", "Utility", "Clutch", "Opening"):
        assert label in ratings
    assert f"Aim         {rating_bar(71.4, 100.0)}    71" in ratings
    assert "+0.12" in ratings and "-0.03" in ratings
    assert f"Opening     {BAR_EMPTY * 10} -0.03" in ratings
    _assert_no_empty_fields(embed)


def test_profile_embed_minimal_falls_back_to_steam_id() -> None:
    embed = build_profile_embed(_minimal_profile())
    assert embed.title == "76561198000000009"
    assert embed.fields == []


def test_profile_embed_partial_data() -> None:
    embed = build_profile_embed(
        _minimal_profile(name="x", aim=100.0, ranks=Ranks(faceit_level=3), total_matches=1234)
    )
    fields = _fields(embed)
    assert list(fields) == [RANKS_FIELD, MATCHES_FIELD, RATINGS_FIELD]
    assert fields[RANKS_FIELD] == "FACEIT: level 3"
    assert fields[MATCHES_FIELD] == "1,234"
    assert fields[RATINGS_FIELD] == f"```\nAim         {BAR_FILLED * 10}   100\n```"
    _assert_no_empty_fields(embed)


def test_profile_embed_empty_ranks_are_omitted() -> None:
    embed = build_profile_embed(_minimal_profile(name="x", ranks=Ranks()))
    assert embed.fields == []
    assert format_ranks(Ranks()) == ""
    assert format_rating_bars(_minimal_profile()) == ""


def test_profile_embed_rating_bars_align() -> None:
    profile = _minimal_profile(aim=5.0, positioning=99.9, utility=0.0, clutch=0.5, opening=-1.0)
    rows = format_rating_bars(profile).splitlines()[1:-1]
    assert len(rows) == 5
    assert len({len(row) for row in rows}) == 1


def test_profile_embed_limits() -> None:
    recent = tuple(
        RecentMatch(
            id=str(i),
            finished_at=None,
            map_name="de_ancient",
            outcome="loss",
            leetify_rating=-0.123,
            score=(0, 13),
        )
        for i in range(50)
    )
    profile = _minimal_profile(
        name="N" * 500,
        winrate=1.0,
        total_matches=10**12,
        aim=999.0,
        positioning=-999.0,
        utility=50.0,
        clutch=-9.99,
        opening=9.99,
        ranks=Ranks(premier=10**9, faceit_level=10, leetify=-99.99),
        recent_matches=recent,
    )
    embed = build_profile_embed(profile)
    assert embed.title == escape_name(profile.name)
    assert len(embed.title) == ESCAPED_NAME_LENGTH
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert len(field.name or "") <= 256
        assert len(field.value or "") <= 1024
    assert len(embed) <= 6000
    _assert_no_empty_fields(embed)
