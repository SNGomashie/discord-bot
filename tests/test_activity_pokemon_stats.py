from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ninja_bot.activity.awards import AwardKey
from ninja_bot.activity.periods import Month
from ninja_bot.activity.pokemon_stats import (
    IMPATIENT_LABEL,
    IMPATIENT_MIN_COOLDOWNS,
    PokemonStats,
    PokemonUserStats,
    ShinyCatch,
    impatient_awards,
    pokemon_stats,
)
from ninja_bot.activity.store import PokemonEvent

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
SEPTEMBER = Month(2026, 9)

_ids = iter(range(1, 10**9))


def ts(day: int, hour: int = 12, month: int = 9) -> float:
    return datetime(2026, month, day, hour, tzinfo=AMSTERDAM).timestamp()


def catch(user_id: int, pokemon: str, when: float, shiny: bool = False) -> PokemonEvent:
    return PokemonEvent(next(_ids), user_id, when, "catch", pokemon, shiny)


def cooldown(user_id: int, when: float) -> PokemonEvent:
    return PokemonEvent(next(_ids), user_id, when, "cooldown")


def test_no_events() -> None:
    stats = pokemon_stats([], SEPTEMBER, AMSTERDAM)
    assert stats == PokemonStats()
    assert not stats.has_data
    assert impatient_awards(stats) == []


def test_counts_per_user_and_ignores_other_months() -> None:
    events = [
        catch(1, "Pikachu", ts(1)),
        catch(1, "Eevee", ts(2), shiny=True),
        cooldown(1, ts(2, 13)),
        catch(2, "Pikachu", ts(3)),
        # Local midnight boundaries: 1 October 00:00 Amsterdam is October.
        catch(3, "Mew", ts(1, 0, month=10)),
        catch(3, "Mew", ts(31, 23, month=8)),
    ]
    stats = pokemon_stats(events, SEPTEMBER, AMSTERDAM)
    assert stats.users == {
        1: PokemonUserStats(catches=2, shinies=1, cooldown_hits=1),
        2: PokemonUserStats(catches=1),
    }
    assert stats.top_species == ("Pikachu",)
    assert stats.top_species_count == 2


def test_shiny_list_is_oldest_first() -> None:
    late = catch(2, "Gyarados", ts(20), shiny=True)
    early = catch(1, "Eevee", ts(5), shiny=True)
    stats = pokemon_stats([late, catch(1, "Ditto", ts(6)), early], SEPTEMBER, AMSTERDAM)
    assert stats.shinies == (
        ShinyCatch(1, "Eevee", early.ts),
        ShinyCatch(2, "Gyarados", late.ts),
    )
    assert stats.users[1].shinies == 1
    assert stats.users[2].shinies == 1


def test_top_species_tie_names_all_in_name_order() -> None:
    events = [
        catch(1, "Zubat", ts(1)),
        catch(2, "Abra", ts(2)),
        catch(1, "Zubat", ts(3)),
        catch(2, "Abra", ts(4)),
        catch(3, "Mew", ts(5)),
    ]
    stats = pokemon_stats(events, SEPTEMBER, AMSTERDAM)
    assert stats.top_species == ("Abra", "Zubat")
    assert stats.top_species_count == 2


def test_month_with_only_cooldowns() -> None:
    events = [cooldown(1, ts(day)) for day in range(1, 4)] + [cooldown(2, ts(1))]
    stats = pokemon_stats(events, SEPTEMBER, AMSTERDAM)
    assert stats.has_data
    assert stats.users[1] == PokemonUserStats(cooldown_hits=3)
    assert stats.top_species == ()
    assert stats.top_species_count == 0
    assert stats.shinies == ()
    awards = impatient_awards(stats)
    assert [(a.key, a.user_id, a.value, a.label) for a in awards] == [
        (AwardKey.IMPATIENT, 1, 3.0, IMPATIENT_LABEL)
    ]


def test_impatient_needs_the_minimum() -> None:
    below = [cooldown(1, ts(day)) for day in range(1, IMPATIENT_MIN_COOLDOWNS)]
    assert impatient_awards(pokemon_stats(below, SEPTEMBER, AMSTERDAM)) == []


def test_impatient_ties_give_one_award_each() -> None:
    events = [cooldown(user_id, ts(day)) for user_id in (7, 3) for day in range(1, 5)]
    events.append(cooldown(9, ts(1)))
    awards = impatient_awards(pokemon_stats(events, SEPTEMBER, AMSTERDAM))
    assert [(a.user_id, a.value) for a in awards] == [(3, 4.0), (7, 4.0)]
