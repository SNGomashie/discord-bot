"""Monthly Toasty `;pokemon` stats and the Impatient award (pure: no discord, no IO)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from ninja_bot.activity.awards import Award, AwardKey
from ninja_bot.activity.periods import Month
from ninja_bot.activity.store import PokemonEvent

IMPATIENT_LABEL = "Impatient"

# The Impatient award needs at least this many cooldown hits.
IMPATIENT_MIN_COOLDOWNS = 3


@dataclass(frozen=True)
class PokemonUserStats:
    catches: int = 0
    shinies: int = 0
    cooldown_hits: int = 0


@dataclass(frozen=True)
class ShinyCatch:
    user_id: int
    pokemon: str
    ts: float


@dataclass(frozen=True)
class PokemonStats:
    users: Mapping[int, PokemonUserStats] = field(default_factory=dict)
    # Most caught species, all tied names in name order; empty without catches.
    top_species: tuple[str, ...] = ()
    top_species_count: int = 0
    shinies: tuple[ShinyCatch, ...] = ()  # oldest first

    @property
    def has_data(self) -> bool:
        return bool(self.users)


def pokemon_stats(events: Iterable[PokemonEvent], month: Month, tz: ZoneInfo) -> PokemonStats:
    """Catches, shinies and cooldown hits per user for `month`; other events are ignored."""
    start = month.start(tz).timestamp()
    end = month.end(tz).timestamp()
    catches: Counter[int] = Counter()
    shinies: Counter[int] = Counter()
    cooldowns: Counter[int] = Counter()
    species: Counter[str] = Counter()
    shiny_catches: list[ShinyCatch] = []
    for event in sorted(events, key=lambda event: (event.ts, event.message_id)):
        if not start <= event.ts < end:
            continue
        if event.kind == "cooldown":
            cooldowns[event.user_id] += 1
            continue
        catches[event.user_id] += 1
        if event.pokemon:
            species[event.pokemon] += 1
        if event.shiny:
            shinies[event.user_id] += 1
            shiny_catches.append(ShinyCatch(event.user_id, event.pokemon or "?", event.ts))

    users = {
        user_id: PokemonUserStats(catches[user_id], shinies[user_id], cooldowns[user_id])
        for user_id in sorted(set(catches) | set(cooldowns))
    }
    top_count = max(species.values(), default=0)
    top_species = tuple(sorted(name for name, count in species.items() if count == top_count))
    return PokemonStats(
        users=users,
        top_species=top_species if top_count else (),
        top_species_count=top_count,
        shinies=tuple(shiny_catches),
    )


def impatient_awards(stats: PokemonStats) -> list[Award]:
    """Most cooldown hits (ties: one `Award` each, lowest id first); none below the minimum."""
    hits = {user_id: user.cooldown_hits for user_id, user in stats.users.items()}
    best = max(hits.values(), default=0)
    if best < IMPATIENT_MIN_COOLDOWNS:
        return []
    return [
        Award(AwardKey.IMPATIENT, user_id, float(best), IMPATIENT_LABEL)
        for user_id in sorted(user_id for user_id, value in hits.items() if value == best)
    ]
