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
    TeamScore,
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
