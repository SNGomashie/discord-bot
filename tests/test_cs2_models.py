import datetime

import pytest

from ninja_bot.cs2.models import (
    ApiError,
    Cs2Error,
    InvalidSteamIdError,
    Match,
    NoMatchesError,
    NotLinkedError,
    PlayerMatchStats,
    PlayerNotFoundError,
    PrivateProfileError,
    RateLimitedError,
    RecentMatch,
    TeamScore,
    newest_first,
)


def _player(steam: str, team: int | None = 2, **kwargs: object) -> PlayerMatchStats:
    return PlayerMatchStats(steam64_id=steam, name=steam, team_number=team, **kwargs)  # type: ignore[arg-type]


def _match(*players: PlayerMatchStats) -> Match:
    return Match(
        id="m1",
        map_name="de_dust2",
        finished_at=None,
        team_scores=(TeamScore(2, 13), TeamScore(3, 7)),
        players=players,
    )


@pytest.mark.parametrize(
    "cls",
    [
        PlayerNotFoundError,
        PrivateProfileError,
        RateLimitedError,
        ApiError,
        InvalidSteamIdError,
        NotLinkedError,
        NoMatchesError,
    ],
)
def test_errors_have_friendly_default_message(cls: type[Cs2Error]) -> None:
    err = cls()
    assert isinstance(err, Cs2Error)
    assert err.message == cls.default_message
    assert err.message != Cs2Error.default_message
    assert str(err) == err.message


def test_error_custom_message() -> None:
    assert ApiError("boom").message == "boom"


def test_derived_stats() -> None:
    p = _player("a", kills=20, deaths=10, hs_kills=5, damage=2400, rounds=24)
    assert p.kd_ratio == 2.0
    assert p.adr == 100.0
    assert p.hs_percent == 25.0


def test_kd_ratio_zero_deaths_is_kills() -> None:
    assert _player("a", kills=7, deaths=0).kd_ratio == 7.0


def test_adr_zero_rounds_is_none() -> None:
    assert _player("a", damage=500, rounds=0).adr is None


def test_hs_percent_zero_kills_is_none() -> None:
    assert _player("a", kills=0, hs_kills=0).hs_percent is None


def test_derived_stats_missing_values_are_none() -> None:
    p = _player("a")
    assert p.kd_ratio is None
    assert p.adr is None
    assert p.hs_percent is None
    assert _player("a", kills=5).kd_ratio is None
    assert _player("a", rounds=10).adr is None
    assert _player("a", kills=5).hs_percent is None


def test_teams_ordered_by_team_number_and_rating() -> None:
    match = _match(
        _player("t3-low", team=3, leetify_rating=-0.02, kills=10),
        _player("t2-none", team=2, leetify_rating=None, kills=30),
        _player("t2-high", team=2, leetify_rating=0.08, kills=12),
        _player("t3-high", team=3, leetify_rating=0.05, kills=15),
        _player("t2-mid-more-kills", team=2, leetify_rating=0.01, kills=20),
        _player("t2-mid-fewer-kills", team=2, leetify_rating=0.01, kills=18),
        _player("t2-mid-no-kills", team=2, leetify_rating=0.01),
    )
    team_a, team_b = match.teams()
    assert [p.steam64_id for p in team_a] == [
        "t2-high",
        "t2-mid-more-kills",
        "t2-mid-fewer-kills",
        "t2-mid-no-kills",
        "t2-none",
    ]
    assert [p.steam64_id for p in team_b] == ["t3-high", "t3-low"]


def test_teams_single_team_and_empty() -> None:
    team_a, team_b = _match(_player("a", team=3)).teams()
    assert [p.steam64_id for p in team_a] == ["a"]
    assert team_b == ()
    assert _match().teams() == ((), ())


def test_teams_skips_players_without_team() -> None:
    team_a, team_b = _match(_player("a", team=2), _player("b", team=None)).teams()
    assert [p.steam64_id for p in team_a] == ["a"]
    assert team_b == ()


def _at(hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 10, 1, hour, tzinfo=datetime.UTC)


def _dated_match(match_id: str, finished_at: datetime.datetime | None) -> Match:
    return Match(id=match_id, map_name=None, finished_at=finished_at, team_scores=(), players=())


def _recent(match_id: str, finished_at: datetime.datetime | None) -> RecentMatch:
    return RecentMatch(
        id=match_id,
        finished_at=finished_at,
        map_name=None,
        outcome=None,
        leetify_rating=None,
        score=None,
    )


def test_newest_first_matches_mixed_dated_and_undated() -> None:
    items = [
        _dated_match("u1", None),
        _dated_match("old", _at(1)),
        _dated_match("u2", None),
        _dated_match("new", _at(5)),
    ]
    assert [m.id for m in newest_first(items)] == ["new", "old", "u1", "u2"]


def test_newest_first_recent_matches_mixed_dated_and_undated() -> None:
    items = [_recent("old", _at(1)), _recent("u1", None), _recent("new", _at(9))]
    assert [m.id for m in newest_first(items)] == ["new", "old", "u1"]


def test_newest_first_equal_timestamps_keep_order() -> None:
    items = [_recent("a", _at(3)), _recent("b", _at(3)), _recent("c", _at(4)), _recent("d", _at(3))]
    assert [m.id for m in newest_first(items)] == ["c", "a", "b", "d"]


def test_newest_first_all_undated_keeps_order() -> None:
    items = [_recent("a", None), _recent("b", None), _recent("c", None)]
    assert [m.id for m in newest_first(items)] == ["a", "b", "c"]


def test_newest_first_empty() -> None:
    assert newest_first([]) == []


def test_newest_first_tz_aware_times_with_undated_items() -> None:
    plus_two = datetime.timezone(datetime.timedelta(hours=2))
    items = [
        _recent("u", None),
        # 12:00+02:00 is 10:00 UTC, so it is older than 11:00 UTC.
        _recent("offset", datetime.datetime(2026, 10, 1, 12, tzinfo=plus_two)),
        _recent("utc", _at(11)),
    ]
    assert [m.id for m in newest_first(items)] == ["utc", "offset", "u"]
