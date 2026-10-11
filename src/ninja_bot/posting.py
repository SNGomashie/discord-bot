"""Shared auto-post plumbing: find the post channel (warning once per cause) and send safely."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

import discord

from ninja_bot.cs2.channels import (
    CHANNEL_MISSING,
    CHANNEL_NOT_TEXT,
    CHANNEL_UNAVAILABLE,
    PostChannel,
    channel_problem,
    is_post_channel,
)

log = logging.getLogger(__name__)


async def send_safely(send: Awaitable[object], what: str = "message") -> bool:
    """Await a Discord send; log and swallow HTTP errors (e.g. an expired token)."""
    try:
        await send
    except discord.HTTPException:
        log.warning("Could not send %s", what, exc_info=True)
        return False
    return True


class PostChannelResolver:
    """Resolves an auto-post channel each cycle; a broken setup warns once per cause.

    `warning_template` has one `%s` for the cause. The remembered causes are cleared
    once the channel works, so a later problem warns again.
    """

    def __init__(self, bot: discord.Client, warning_template: str, channel_id: int) -> None:
        self.bot = bot
        self.warning_template = warning_template
        self.channel_id = channel_id
        self._warned: set[str] = set()

    async def resolve(self) -> PostChannel | None:
        """The channel, or None (warned once per cause) if it can't be used this cycle."""
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden):
                self._warn(CHANNEL_MISSING)
                return None
            except discord.HTTPException:
                # A Discord outage, not a setup problem; the next cycle tries again.
                self._warn(CHANNEL_UNAVAILABLE)
                return None
        if not is_post_channel(channel):
            self._warn(CHANNEL_NOT_TEXT)
            return None
        problem = channel_problem(channel, channel.guild.me)
        if problem is not None:
            self._warn(problem)
            return None
        self._warned.clear()
        return channel

    def _warn(self, cause: str) -> None:
        if cause in self._warned:
            return
        self._warned.add(cause)
        log.warning(self.warning_template, cause)
