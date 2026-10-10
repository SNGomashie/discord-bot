import pytest

from ninja_bot.cs2.awards import Award, compute_awards
from ninja_bot.cs2.models import Match, PlayerMatchStats


def _player(index: int, **kwargs: object) -> PlayerMatchStats:
    defaults: dict[str, object] = {"name": f"player{index}", "kills": 10}
    defaults.update(kwargs)
    return PlayerMatchStats(steam64_id=f"7656119{index:010d}", **defaults)  # type: ignore[arg-type]


def _match(*players: PlayerMatchStats) -> Match:
    return Match(id="m1", map_name="de_mirage", finished_at=None, team_scores=(), players=players)


def _titles(awards: list[Award]) -> list[str]:
    return [a.title for a in awards]


def _only(match: Match, title: str) -> Award:
    found = [a for a in compute_awards(match, limit=10) if a.title == title]
    assert len(found) == 1
    return found[0]


def test_headhunter_needs_five_kills() -> None:
    match = _match(
        _player(1, kills=4, hs_kills=4),  # 100% but too few kills
        _player(2, kills=20, hs_kills=12),
        _player(3, kills=10, hs_kills=5),
    )
    award = _only(match, "Headhunter")
    assert award == Award("Headhunter", "76561190000000002", "player2", "60% headshots")


def test_utility_wizard() -> None:
    match = _match(_player(1, flash_assists=0), _player(2, flash_assists=3))
    assert _only(match, "Utility Wizard").detail == "3 flash assists"
    assert _only(_match(_player(1, flash_assists=1)), "Utility Wizard").detail == "1 flash assist"
    assert compute_awards(_match(_player(1, flash_assists=0))) == []


def test_trader_needs_two() -> None:
    assert compute_awards(_match(_player(1, trade_kills=1))) == []
    award = _only(_match(_player(1, trade_kills=2), _player(2, trade_kills=4)), "Trader")
    assert (award.name, award.detail) == ("player2", "4 trade kills")


def test_ace_hunter_counts_three_plus_rounds() -> None:
    match = _match(
        _player(1, multi3k=1, multi4k=1, multi5k=1),
        _player(2, multi3k=2),
        _player(3, multi2k=5),
    )
    award = _only(match, "Ace Hunter")
    assert (award.name, award.detail) == ("player1", "3 rounds with 3+ kills")
    assert compute_awards(_match(_player(1, multi3k=0, multi4k=None))) == []
    assert _only(_match(_player(1, multi5k=1)), "Ace Hunter").detail == "1 round with 3+ kills"


def test_lightning_reflexes_lowest_wins() -> None:
    match = _match(_player(1, reaction_time_ms=520.0), _player(2, reaction_time_ms=410.4))
    award = _only(match, "Lightning Reflexes")
    assert (award.name, award.detail) == ("player2", "410 ms reactions")


def test_ghost_uses_survival_percentage() -> None:
    match = _match(
        _player(1, rounds_survived=12, rounds=24),
        _player(2, rounds_survived=14, rounds=20),
        _player(3, rounds_survived=5, rounds=0),
    )
    award = _only(match, "Ghost")
    assert (award.name, award.detail) == ("player2", "survived 70% of rounds")


def test_whiffer_needs_ten_kills() -> None:
    match = _match(
        _player(1, kills=9, accuracy=5.0),
        _player(2, kills=12, accuracy=18.4),
        _player(3, kills=15, accuracy=25.0),
    )
    award = _only(match, "Whiffer")
    assert (award.name, award.detail) == ("player2", "18% accuracy")


def test_friendly_fire_hazard_needs_three() -> None:
    assert compute_awards(_match(_player(1, shots_hit_friend=2))) == []
    award = _only(_match(_player(1, shots_hit_friend=7)), "Friendly Fire Hazard")
    assert award.detail == "7 shots into teammates"


def test_ties_broken_by_kills_then_name() -> None:
    by_kills = _match(
        _player(1, kills=10, trade_kills=3),
        _player(2, kills=15, trade_kills=3),
    )
    assert _only(by_kills, "Trader").name == "player2"
    by_name = _match(
        _player(1, name="zed", trade_kills=3),
        _player(2, name="Alpha", trade_kills=3),
    )
    assert _only(by_name, "Trader").name == "Alpha"


def test_player_capped_at_two_awards() -> None:
    star = _player(1, kills=30, hs_kills=20, flash_assists=5, trade_kills=6)
    other = _player(2, kills=10, hs_kills=2, flash_assists=1, trade_kills=2)
    awards = compute_awards(_match(star, other))
    assert [(a.title, a.name) for a in awards] == [
        ("Headhunter", "player1"),
        ("Utility Wizard", "player1"),
        ("Trader", "player2"),
    ]


def test_missing_data_skips_categories() -> None:
    match = _match(_player(1, kills=None), _player(2, kills=None))
    assert compute_awards(match) == []


def test_empty_match() -> None:
    assert compute_awards(_match()) == []


def _full_match() -> Match:
    return _match(
        _player(1, kills=20, hs_kills=10, flash_assists=2),
        _player(2, kills=15, trade_kills=3, multi3k=2),
        _player(3, kills=11, reaction_time_ms=400.0, rounds_survived=12, rounds=20),
        _player(4, kills=10, accuracy=12.0, shots_hit_friend=4),
    )


def test_priority_order_and_limit() -> None:
    every = [
        "Headhunter",
        "Utility Wizard",
        "Trader",
        "Ace Hunter",
        "Lightning Reflexes",
        "Ghost",
        "Whiffer",
        "Friendly Fire Hazard",
    ]
    awards = compute_awards(_full_match(), limit=10)
    assert _titles(awards) == every
    assert _titles(compute_awards(_full_match())) == every[:5]
    assert _titles(compute_awards(_full_match(), limit=2)) == every[:2]
    assert compute_awards(_full_match(), limit=0) == []


@pytest.mark.parametrize("limit", [1, 3, 8])
def test_no_player_exceeds_cap(limit: int) -> None:
    awards = compute_awards(_full_match(), limit=limit)
    names = [a.name for a in awards]
    assert all(names.count(n) <= 2 for n in names)


def test_name_falls_back_to_steam_id() -> None:
    award = _only(_match(_player(1, name=None, trade_kills=2)), "Trader")
    assert award.name == "76561190000000001"
