from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from ninja_bot.cs2.models import PlayerMatchStats
from ninja_bot.cs2.store import LinkStore

log = logging.getLogger(__name__)

# One match has at most ten players, so never look up more members than that.
MAX_LOOKUPS = 10

# Discord user ID -> the member's display name, or None if they aren't in the server.
MemberLookup = Callable[[int], Awaitable[str | None]]


@dataclass(frozen=True)
class Identity:
    steam64_id: str
    discord_id: int
    display_name: str


async def _lookup_safely(lookup: MemberLookup, discord_id: int) -> str | None:
    """A failing lookup counts as "not a member" and never affects the others."""
    try:
        return await lookup(discord_id)
    except Exception as exc:
        log.warning("Could not look up a linked Discord member: %s", type(exc).__name__)
        return None


async def resolve_identities(
    players: Sequence[PlayerMatchStats], links: LinkStore, lookup: MemberLookup
) -> dict[str, Identity]:
    """Steam ID -> Identity for players linked to a member of this server."""
    steam64_ids = list(dict.fromkeys(player.steam64_id for player in players))
    if not steam64_ids:
        return {}
    linked = await links.discord_ids_for(steam64_ids)
    discord_ids = sorted(set(linked.values()))[:MAX_LOOKUPS]
    names = await asyncio.gather(*(_lookup_safely(lookup, i) for i in discord_ids))
    display_names = dict(zip(discord_ids, names, strict=True))
    result: dict[str, Identity] = {}
    for steam64_id in steam64_ids:
        discord_id = linked.get(steam64_id)
        name = display_names.get(discord_id) if discord_id is not None else None
        if discord_id is not None and name is not None:
            result[steam64_id] = Identity(steam64_id, discord_id, name)
    return result
