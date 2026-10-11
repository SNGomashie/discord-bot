import dataclasses
import datetime
import json
from pathlib import Path

import discord
import pytest

from ninja_bot.cs2 import embed as embed_module
from ninja_bot.cs2.awards import Award, compute_awards
from ninja_bot.cs2.embed import (
    AWARDS_FIELD,
    BREAKDOWN_FIELD,
    FORM_FIELD,
    FRIENDS_FIELD,
    LEADERBOARD_CONTINUED_FIELD,
    LEADERBOARD_FIELD,
    MATCHES_FIELD,
    MAX_LEADERBOARD_ROWS,
    NO_TEAM,
    NOBODY_LINKED_MESSAGE,
    OTHER_PLAYERS_FIELD,
    PROFILE_COLOUR,
    RANKS_FIELD,
    RATINGS_FIELD,
    UNRANKED,
    WINRATE_FIELD,
    YOUR_TEAM,
    build_leaderboard_embed,
    build_match_embed,
    build_profile_embed,
    format_breakdown,
    format_friends,
    format_leaderboard_row,
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
from ninja_bot.cs2.identities import Identity
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
from ninja_bot.cs2.squad import LeaderboardRow, Metric, Trend, metric_label

FIXTURES = Path(__file__).parent / "fixtures" / "leetify"


@pytest.fixture
def match() -> Match:
    return parse_match(json.loads((FIXTURES / "match.json").read_text()))


@pytest.fixture
def profile() -> Profile:
    return parse_profile(json.loads((FIXTURES / "profile.json").read_text()))


def _player(index: int, team: int | None, **kwargs: object) -> PlayerMatchStats:
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


def _unassigned_match(*players: PlayerMatchStats) -> Match:
    return Match(
        id="m2",
        map_name="de_inferno",
        finished_at=None,
        team_scores=(TeamScore(2, 13), TeamScore(3, 9)),
        players=players,
    )


def test_all_players_assigned_has_no_other_players_field(match: Match) -> None:
    embed = build_match_embed(match, [])
    assert OTHER_PLAYERS_FIELD not in _fields(embed)


def test_players_without_team_number_get_other_players_field() -> None:
    match = _unassigned_match(_player(1, 2), _player(2, 3), _player(3, None))
    embed = build_match_embed(match, [])
    assert [f.name for f in embed.fields] == ["Team A", "Team B", OTHER_PLAYERS_FIELD]
    other = embed.fields[2]
    assert not other.inline
    assert "player3" in (other.value or "")
    assert "player3" not in (embed.fields[0].value or "") + (embed.fields[1].value or "")
    _assert_no_empty_fields(embed)


def test_third_team_number_goes_to_other_players() -> None:
    match = _unassigned_match(_player(1, 2), _player(2, 3), _player(3, 4), _player(4, None))
    fields = _fields(build_match_embed(match, [Award("Top", "7656119", "player1", "x")]))
    assert list(fields)[:4] == ["Team A", "Team B", OTHER_PLAYERS_FIELD, AWARDS_FIELD]
    assert "player3" in fields[OTHER_PLAYERS_FIELD]
    assert "player4" in fields[OTHER_PLAYERS_FIELD]


def test_no_team_numbers_lists_everyone_with_neutral_colour() -> None:
    match = _unassigned_match(*(_player(i, None) for i in range(4)))
    embed = build_match_embed(match, [], match.players[0].steam64_id)
    assert embed.colour == discord.Colour.light_grey()
    names = [f.name for f in embed.fields]
    assert names[:3] == ["Team A", "Team B", OTHER_PLAYERS_FIELD]
    other = embed.fields[2].value or ""
    assert all(f"player{i}" in other for i in range(4))
    _assert_no_empty_fields(embed)


def test_unassigned_focus_player_still_gets_details(profile: Profile) -> None:
    focus = _player(3, None, kills=20, deaths=10, assists=4)
    match = _unassigned_match(_player(1, 2), _player(2, 3), focus)
    embed = build_match_embed(match, [], focus.steam64_id, profile)
    assert embed.colour == discord.Colour.light_grey()
    names = [f.name for f in embed.fields]
    assert names[:3] == ["Team A", "Team B", OTHER_PLAYERS_FIELD]
    assert YOUR_TEAM not in names
    fields = _fields(embed)
    assert fields["player3"].startswith("K/D/A: 20/10/4")
    assert BREAKDOWN_FIELD in fields
    assert FORM_FIELD in fields
    _assert_no_empty_fields(embed)


def test_limits_ten_unassigned_players_with_long_names(profile: Profile) -> None:
    long_name = "W" * 32
    players = tuple(
        _player(
            i,
            None,
            name=f"{long_name[:-2]}{i:02d}",
            kills=99,
            deaths=99,
            assists=99,
            hs_kills=99,
            damage=99999,
            rounds=30,
            mvps=i,
            leetify_rating=-0.123,
            multi2k=9,
            multi3k=9,
            multi4k=9,
            multi5k=9,
            reaction_time_ms=999.0,
            preaim=99.9,
        )
        for i in range(10)
    )
    match = Match(
        id="m",
        map_name="de_" + "x" * 300,
        finished_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        team_scores=(TeamScore(2, 16), TeamScore(3, 14)),
        players=players,
    )
    awards = compute_awards(match, limit=25)
    long_profile = dataclasses.replace(profile, steam64_id=players[0].steam64_id, name=long_name)

    embed = build_match_embed(match, awards, players[0].steam64_id, long_profile)

    fields = _fields(embed)
    assert OTHER_PLAYERS_FIELD in fields
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


def _identity(player: PlayerMatchStats, display_name: str) -> Identity:
    return Identity(player.steam64_id, 1000 + int(player.steam64_id[-10:]), display_name)


@pytest.mark.parametrize("identities", [None, {}])
def test_match_embed_without_identities_is_unchanged(
    match: Match, profile: Profile, identities: dict[str, Identity] | None
) -> None:
    focus = match.players[0].steam64_id
    plain = build_match_embed(match, compute_awards(match), focus, profile)
    embed = build_match_embed(match, compute_awards(match), focus, profile, identities)
    assert embed.to_dict() == plain.to_dict()


def test_identities_name_players_in_every_table() -> None:
    players = (_player(1, 2), _player(2, 3), _player(3, None), _player(4, 2))
    match = _unassigned_match(*players)
    identities = {
        p.steam64_id: _identity(p, name)
        for p, name in zip(players[:3], ("Alice", "Bob", "Carol"), strict=True)
    }

    fields = _fields(build_match_embed(match, [], identities=identities))

    assert "Alice" in fields["Team A"]
    assert "player4" in fields["Team A"]
    assert "Bob" in fields["Team B"]
    assert "Carol" in fields[OTHER_PLAYERS_FIELD]
    tables = "".join(fields[name] for name in ("Team A", "Team B", OTHER_PLAYERS_FIELD))
    assert not any(f"player{i}" in tables for i in (1, 2, 3))


def _friends_match(*players: PlayerMatchStats) -> Match:
    return Match(
        id="m",
        map_name="de_dust2",
        finished_at=None,
        team_scores=(TeamScore(2, 13), TeamScore(3, 7)),
        players=players,
    )


def test_no_friends_has_no_friends_field(match: Match) -> None:
    fields = _fields(build_match_embed(match, [], match.players[0].steam64_id, identities={}))
    assert FRIENDS_FIELD not in fields


def test_one_friend_line() -> None:
    players = (_player(1, 2), _player(2, 3))
    identity = _identity(players[1], "Bob")
    match = _friends_match(*players)

    embed = build_match_embed(match, [], identities={players[1].steam64_id: identity})

    fields = _fields(embed)
    assert fields[FRIENDS_FIELD] == f"<@{identity.discord_id}> as player2 (Team B)"
    names = [f.name for f in embed.fields]
    assert names == ["Team A", "Team B", FRIENDS_FIELD]
    assert not any(f.inline for f in embed.fields if f.name == FRIENDS_FIELD)


def test_requester_and_friends(profile: Profile) -> None:
    players = (
        _player(1, 2, leetify_rating=0.01),
        _player(2, 2, leetify_rating=0.05),
        _player(3, 3, leetify_rating=0.09),
        _player(4, 2, leetify_rating=None),
        _player(5, None),
    )
    identities = {p.steam64_id: _identity(p, f"D{i}") for i, p in enumerate(players)}
    match = _friends_match(*players)
    awards = [Award("MVP", players[0].steam64_id, players[0].name or "", "x")]

    embed = build_match_embed(match, awards, players[0].steam64_id, profile, identities)

    names = [f.name for f in embed.fields]
    assert names.index(AWARDS_FIELD) < names.index(FRIENDS_FIELD) < names.index("player1")
    fields = _fields(embed)
    ids = [identities[p.steam64_id].discord_id for p in players]
    assert fields[FRIENDS_FIELD].splitlines() == [
        f"<@{ids[1]}> as player2 (Your team)",
        f"<@{ids[0]}> as player1 (Your team)",
        f"<@{ids[3]}> as player4 (Your team)",
        f"<@{ids[2]}> as player3 (Team B)",
        f"<@{ids[4]}> as player5 ({NO_TEAM})",
    ]
    assert fields["player1"].startswith(f"<@{ids[0]}> · player1\n")
    # Only the requester gets detail fields.
    assert not any(f"player{i}" in names for i in (2, 3, 4, 5))
    assert names.count(BREAKDOWN_FIELD) == 1
    assert names.count(FORM_FIELD) == 1


def test_focus_field_unchanged_without_requester_identity(profile: Profile) -> None:
    players = (_player(1, 2, kills=5), _player(2, 3))
    match = _friends_match(*players)
    identities = {players[1].steam64_id: _identity(players[1], "Bob")}

    plain = _fields(build_match_embed(match, [], players[0].steam64_id, profile))
    fields = _fields(build_match_embed(match, [], players[0].steam64_id, profile, identities))

    assert fields["player1"] == plain["player1"] == "K/D/A: 5/-/-"


def test_focus_mention_without_stats() -> None:
    players = (_player(1, 2), _player(2, 3))
    match = _friends_match(*players)
    identity = _identity(players[0], "Al")

    fields = _fields(
        build_match_embed(
            match, [], players[0].steam64_id, identities={players[0].steam64_id: identity}
        )
    )

    assert fields["player1"] == f"<@{identity.discord_id}> · player1"


def test_friend_names_are_escaped() -> None:
    players = (_player(1, 2, name="*bold* [x](http://e.com) @everyone"), _player(2, 3))
    match = _friends_match(*players)
    identity = _identity(players[0], "Al")

    fields = _fields(build_match_embed(match, [], identities={players[0].steam64_id: identity}))

    name = escape_name(players[0].name or "")
    assert fields[FRIENDS_FIELD] == f"<@{identity.discord_id}> as {name} (Team A)"
    assert "*bold*" not in fields[FRIENDS_FIELD]


def test_ten_friends_with_long_names_fit_the_limits(profile: Profile) -> None:
    hostile = "*_~`|[]()" * 4  # every character gets escaped
    players = tuple(
        _player(i, 2 if i < 5 else 3, name=hostile[:30] + f"{i:02d}", kills=9, leetify_rating=0.1)
        for i in range(10)
    )
    identities = {
        p.steam64_id: Identity(p.steam64_id, 18446744073709551615 - i, "W" * 32)
        for i, p in enumerate(players)
    }
    match = _friends_match(*players)
    awards = compute_awards(match, limit=25)

    embed = build_match_embed(match, awards, players[0].steam64_id, profile, identities)

    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert len(field.name or "") <= 256
        assert len(field.value or "") <= 1024
    assert len(embed) <= 6000
    _assert_no_empty_fields(embed)
    lines = _fields(embed)[FRIENDS_FIELD].splitlines()
    # Whole lines only: every line is a full mention line or the "+N more" tail.
    assert all(line.startswith("<@") and line.endswith(")") for line in lines[:-1])
    assert lines[-1].startswith("+") or len(lines) == 10
    shown = len(lines) - (1 if lines[-1].startswith("+") else 0)
    if shown < 10:
        assert lines[-1] == f"+{10 - shown} more"
    # Only the requester has a detail field.
    detail_fields = [
        f for f in embed.fields if f.value and f.value.startswith("<@") and " · " in f.value
    ]
    assert len(detail_fields) == 1


def test_friends_lines_capped_at_ten() -> None:
    players = tuple(_player(i, 2 if i % 2 else 3) for i in range(12))
    identities = {p.steam64_id: _identity(p, "x") for p in players}

    fields = _fields(build_match_embed(_friends_match(*players), [], identities=identities))

    assert len(fields[FRIENDS_FIELD].splitlines()) == 10


def test_friends_lines_dropped_whole_over_the_field_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    players = tuple(_player(i, 2) for i in range(4))
    identities = {p.steam64_id: _identity(p, "x") for p in players}
    monkeypatch.setattr("ninja_bot.cs2.embed.FIELD_VALUE_LIMIT", 100)

    value = format_friends(_friends_match(*players), identities)

    assert len(value) <= 100
    lines = value.splitlines()
    assert lines[-1] == f"+{4 - (len(lines) - 1)} more"
    assert all(line.endswith("(Team A)") for line in lines[:-1])


def leaderboard_row(
    rank: int | None,
    discord_id: int,
    name: str,
    value: float | None,
    trend: Trend | None = None,
) -> LeaderboardRow:
    return LeaderboardRow(rank=rank, discord_id=discord_id, name=name, value=value, trend=trend)


def test_leaderboard_embed_lists_ranked_rows() -> None:
    trend = Trend(direction="up", delta=0.03, streak_kind="win", streak_length=3)
    rows = [
        leaderboard_row(1, 11, "Alice", 0.05, trend),
        leaderboard_row(2, 22, "Bob", 0.02),
        leaderboard_row(3, 33, "Carol", 0.0),
        leaderboard_row(4, 44, "Dave", -0.04),
    ]

    embed = build_leaderboard_embed(rows, Metric.RATING)

    assert embed.title is not None
    assert metric_label(Metric.RATING) in embed.title
    assert [f.name for f in embed.fields] == [LEADERBOARD_FIELD]
    lines = (embed.fields[0].value or "").split("\n")
    assert lines == [
        "🥇 <@11> Alice · **+0.05** ↑ +0.03 🔥3",
        "🥈 <@22> Bob · **+0.02**",
        "🥉 <@33> Carol · **+0.00**",
        "4. <@44> Dave · **-0.04**",
    ]
    assert embed.footer.text is None


def test_leaderboard_embed_uses_metric_formatting() -> None:
    embed = build_leaderboard_embed([leaderboard_row(1, 11, "Alice", 0.62)], Metric.WINRATE)

    assert embed.fields[0].value == "🥇 <@11> Alice · **62%**"


def test_leaderboard_embed_shares_medals_for_ties() -> None:
    rows = [
        leaderboard_row(1, 11, "Alice", 70),
        leaderboard_row(1, 22, "Bob", 70),
        leaderboard_row(3, 33, "Carol", 60),
        leaderboard_row(4, 44, "Dave", 50),
    ]

    lines = (build_leaderboard_embed(rows, Metric.AIM).fields[0].value or "").split("\n")

    assert [line.split(" ")[0] for line in lines] == ["🥇", "🥇", "🥉", "4."]


def test_leaderboard_embed_shows_dash_for_missing_values() -> None:
    rows = [leaderboard_row(1, 11, "Alice", 0.05), leaderboard_row(None, 22, "Bob", None)]

    lines = (build_leaderboard_embed(rows, Metric.CLUTCH).fields[0].value or "").split("\n")

    assert lines[1] == f"{UNRANKED} <@22> Bob · **-**"


def test_leaderboard_embed_escapes_names() -> None:
    embed = build_leaderboard_embed([leaderboard_row(1, 11, "*bold*", 0.05)], Metric.RATING)

    assert escape_name("*bold*") in (embed.fields[0].value or "")


def test_leaderboard_embed_empty_points_to_link() -> None:
    embed = build_leaderboard_embed([], Metric.RATING)

    assert embed.fields == []
    assert embed.description == NOBODY_LINKED_MESSAGE
    assert "/link" in embed.description


def test_leaderboard_embed_caps_rows_and_splits_fields() -> None:
    trend = Trend(direction="down", delta=-0.12, streak_kind="loss", streak_length=12)
    # Every character escapes, so names are as long as escaping can make them.
    name = "_" * 32
    rows = [
        leaderboard_row(i + 1, 10**19 + i, name, -0.15, trend)
        for i in range(MAX_LEADERBOARD_ROWS + 5)
    ]

    embed = build_leaderboard_embed(rows, Metric.OPENING)

    assert len(embed) <= 6000
    assert len(embed.fields) > 1
    assert all(len(f.value or "") <= 1024 for f in embed.fields)
    lines = [line for f in embed.fields for line in (f.value or "").split("\n")]
    assert len(lines) == MAX_LEADERBOARD_ROWS
    assert all(line.startswith(f"{i + 1}. ") or i < 3 for i, line in enumerate(lines))
    assert lines[-1].startswith(f"{MAX_LEADERBOARD_ROWS}. <@{10**19 + 24}>")
    assert embed.footer.text == "+5 more"


def test_leaderboard_embed_fields_hold_whole_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embed_module, "FIELD_VALUE_LIMIT", 60)
    rows = [leaderboard_row(i + 1, 11 + i, f"Player{i}", 0.01) for i in range(6)]
    expected = [format_leaderboard_row(row, Metric.RATING) for row in rows]

    embed = build_leaderboard_embed(rows, Metric.RATING)

    assert all(len(f.value or "") <= 60 for f in embed.fields)
    assert [f.name for f in embed.fields][1:] == [LEADERBOARD_CONTINUED_FIELD] * (
        len(embed.fields) - 1
    )
    assert [line for f in embed.fields for line in (f.value or "").split("\n")] == expected
