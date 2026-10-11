"""Finds new matches of linked players for automatic scoreboard posts."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ninja_bot.cs2.client import StatsProvider, full_match
from ninja_bot.cs2.models import Cs2Error, Match, RateLimitedError
from ninja_bot.cs2.squad import SquadMember
from ninja_bot.cs2.store import LinkStore, MatchLog

log = logging.getLogger(__name__)

MAX_AGE_SECONDS = 10800
MAX_PER_CYCLE = 3


class PollerStore(LinkStore, MatchLog, Protocol):
    """Links plus the match log (`SqliteLinkStore` implements both)."""


@dataclass(frozen=True)
class NewMatch:
    """A match to announce, with every linked player who played in it."""

    match: Match
    squad: tuple[SquadMember, ...]


class MatchPoller:
    """Collects unposted matches of linked players; never marks them posted itself.

    Players seen for the first time are seeded silently (their current history is
    recorded as posted). The caller announces each `NewMatch` and then calls
    `confirm`, whether the send worked or not, so a broken post is never retried.
    """

    def __init__(
        self,
        provider: StatsProvider,
        store: PollerStore,
        clock: Callable[[], float] = time.time,
        max_age_seconds: float = MAX_AGE_SECONDS,
        max_per_cycle: int = MAX_PER_CYCLE,
    ) -> None:
        self._provider = provider
        self._store = store
        self._clock = clock
        self._max_age_seconds = max_age_seconds
        self._max_per_cycle = max_per_cycle

    async def poll(self) -> list[NewMatch]:
        links = await self._store.all_links()
        # Steam ID -> lowest linked Discord ID, in Discord ID order.
        members: dict[str, int] = {}
        for discord_id in sorted(links):
            members.setdefault(links[discord_id], discord_id)

        candidates: dict[str, Match] = {}
        for steam64_id in members:
            try:
                matches = await self._provider.get_recent_matches(steam64_id)
            except RateLimitedError:
                log.info("Leetify rate limited the match poller; stopping this cycle")
                break
            except Cs2Error as exc:
                log.info("Skipping a player in the match poller (%s)", type(exc).__name__)
                continue
            for match in await self._new_matches(steam64_id, matches):
                candidates.setdefault(match.id, match)

        fresh = await self._drop_stale(list(candidates.values()))
        result: list[NewMatch] = []
        for match in sorted(fresh, key=_oldest_first_key):
            if len(result) >= self._max_per_cycle:
                break
            try:
                full = await full_match(self._provider, match, strict=True)
            except RateLimitedError:
                log.info("Leetify rate limited the match poller; stopping this cycle")
                break
            except Cs2Error as exc:
                # Not marked posted: retried next cycle until the age limit drops it.
                log.info("Skipping match %s in the match poller (%s)", match.id, type(exc).__name__)
                continue
            result.append(NewMatch(full, _squad(full, members)))
        return result

    async def confirm(self, new_match: NewMatch) -> None:
        await self._store.mark_posted([new_match.match.id])

    async def _new_matches(self, steam64_id: str, matches: list[Match]) -> list[Match]:
        ids = [match.id for match in matches]
        if not await self._store.is_seeded(steam64_id):
            await self._store.mark_posted(ids)
            await self._store.mark_seeded(steam64_id)
            return []
        unposted = set(await self._store.filter_unposted(ids))
        return [match for match in matches if match.id in unposted]

    async def _drop_stale(self, matches: list[Match]) -> list[Match]:
        cutoff = self._clock() - self._max_age_seconds
        stale = [
            match.id
            for match in matches
            if match.finished_at is not None and match.finished_at.timestamp() < cutoff
        ]
        await self._store.mark_posted(stale)
        return [match for match in matches if match.id not in stale]


def _oldest_first_key(match: Match) -> tuple[bool, float]:
    """Dated matches oldest first; undated ones last (stable)."""
    if match.finished_at is None:
        return (True, 0.0)
    return (False, match.finished_at.timestamp())


def _squad(match: Match, members: dict[str, int]) -> tuple[SquadMember, ...]:
    """Linked players present in `match`, in match player order, one entry per Steam ID."""
    seen: set[str] = set()
    squad: list[SquadMember] = []
    for player in match.players:
        steam64_id = player.steam64_id
        if steam64_id in members and steam64_id not in seen:
            seen.add(steam64_id)
            squad.append(SquadMember(members[steam64_id], steam64_id))
    return tuple(squad)
