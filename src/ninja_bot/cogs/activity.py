from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from ninja_bot.activity.store import ActivityStore, SqliteActivityStore
from ninja_bot.activity.tracker import Close, Move, Nothing, Open, plan_transition

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60.0


def _channel_id(state: discord.VoiceState) -> int | None:
    return None if state.channel is None else state.channel.id


def _afk_channel_id(guild: discord.Guild) -> int | None:
    return None if guild.afk_channel is None else guild.afk_channel.id


def members_in_voice(guilds: Iterable[discord.Guild]) -> list[tuple[int, int]]:
    """(user_id, channel_id) of every non-bot member in a non-AFK voice or stage channel."""
    found: list[tuple[int, int]] = []
    for guild in guilds:
        afk = _afk_channel_id(guild)
        channels: list[discord.VoiceChannel | discord.StageChannel] = [
            *guild.voice_channels,
            *guild.stage_channels,
        ]
        for channel in channels:
            if channel.id == afk:
                continue
            found.extend((member.id, channel.id) for member in channel.members if not member.bot)
    return found


class Activity(commands.Cog):
    """Records voice time per member; all decisions live in `ninja_bot.activity`."""

    def __init__(
        self,
        bot: commands.Bot,
        store: ActivityStore,
        guild_id: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.store = store
        self.guild_id = guild_id
        self.clock = clock
        self._reconciled = False

    async def cog_unload(self) -> None:
        self.heartbeat_loop.cancel()

    def tracked_guilds(self) -> list[discord.Guild]:
        """The `GUILD_ID` guild when set, else every guild the bot is in."""
        if self.guild_id is None:
            return list(self.bot.guilds)
        guild = self.bot.get_guild(self.guild_id)
        return [] if guild is None else [guild]

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # on_ready fires again after reconnects; only reconcile once per run.
        if self._reconciled:
            return
        self._reconciled = True
        await self.reconcile()
        if not self.heartbeat_loop.is_running():
            self.heartbeat_loop.start()

    async def reconcile(self) -> None:
        """Close sessions left by a previous run at their last heartbeat, then reopen live ones."""
        try:
            closed = await self.store.close_stale()
            if closed:
                log.info("Closed %d voice session(s) left from a previous run", closed)
            now = self.clock()
            for user_id, channel_id in members_in_voice(self.tracked_guilds()):
                await self.store.open_session(user_id, channel_id, now)
        except Exception:
            log.exception("Failed to reconcile voice sessions")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        if self.guild_id is not None and member.guild.id != self.guild_id:
            return
        action = plan_transition(
            _channel_id(before), _channel_id(after), _afk_channel_id(member.guild)
        )
        try:
            now = self.clock()
            match action:
                case Open(channel) | Move(channel):
                    # open_session closes any open session first, so Move = close then open.
                    await self.store.open_session(member.id, channel, now)
                case Close():
                    await self.store.close_session(member.id, now)
                case Nothing():
                    pass
        except Exception:
            log.exception("Failed to record a voice state change")

    @tasks.loop(seconds=HEARTBEAT_SECONDS)
    async def heartbeat_loop(self) -> None:
        try:
            await self.store.heartbeat(self.clock())
        except Exception:
            log.exception("Voice session heartbeat failed")

    @heartbeat_loop.before_loop
    async def _before_heartbeat(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: NinjaBot) -> None:
    store = SqliteActivityStore(Path(bot.config.data_dir) / "activity.sqlite3")
    await bot.add_cog(Activity(bot, store, bot.config.guild_id))
