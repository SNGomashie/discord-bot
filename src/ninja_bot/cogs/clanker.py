"""Defensive replies when someone says "clanker"; only added with `CLANKER_REPLIES`.

Anyone who answers one of those replies gets a short timeout (needs the Moderate Members
permission; without it, or for members the bot can't moderate, the taunt is sent anyway).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ninja_bot.clanker.content import CLANKER_REPLIES, TIMEOUT_REPLIES
from ninja_bot.clanker.logic import (
    TIMEOUT_SECONDS,
    ClankerCooldown,
    ReplyTracker,
    mentions_clanker,
)
from ninja_bot.flavor import LinePicker
from ninja_bot.posting import send_safely

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot


class Clanker(commands.Cog):
    """Thin wrapper: all decisions live in `ninja_bot.clanker`."""

    def __init__(
        self,
        guild_id: int | None = None,
        picker: LinePicker | None = None,
        cooldown: ClankerCooldown | None = None,
        replies: ReplyTracker | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.picker = picker if picker is not None else LinePicker()
        self.cooldown = cooldown if cooldown is not None else ClankerCooldown()
        self.replies = replies if replies is not None else ReplyTracker()

    def _in_scope(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        return self.guild_id is None or message.guild.id == self.guild_id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self._in_scope(message):
            return
        reference = message.reference
        if reference is not None and self.replies.is_reply_to_ours(reference.message_id):
            await self._punish(message)
            return
        if not mentions_clanker(message.content):
            return
        if not self.cooldown.allow(message.channel.id):
            return
        try:
            sent = await message.reply(
                self.picker.pick(CLANKER_REPLIES),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            return
        self.replies.remember(sent.id)

    async def _punish(self, message: discord.Message) -> None:
        """Time out whoever answered a clanker reply and taunt them about it."""
        author = message.author
        if isinstance(author, discord.Member):
            await send_safely(
                author.timeout(
                    timedelta(seconds=TIMEOUT_SECONDS), reason="Answered a clanker reply"
                ),
                "a timeout",
            )
        await send_safely(
            message.reply(
                self.picker.pick(TIMEOUT_REPLIES),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            ),
            "a timeout taunt",
        )


async def setup(bot: NinjaBot) -> None:
    if bot.config.clanker_replies:
        await bot.add_cog(Clanker(bot.config.guild_id))
