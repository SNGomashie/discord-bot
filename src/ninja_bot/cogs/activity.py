from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ninja_bot.activity.embed import build_monthly_embed, build_profile_embed
from ninja_bot.activity.formatting import month_label
from ninja_bot.activity.periods import InvalidMonthError, Month, resolve_month
from ninja_bot.activity.pokemon import Catch, PendingCommands, is_pokemon_command, parse_reply
from ninja_bot.activity.report import (
    SHAME_LIMIT,
    MonthlyReport,
    build_monthly_report,
    user_profile,
    without_departed,
)
from ninja_bot.activity.reporter import MonthlyReporter
from ninja_bot.activity.store import ActivityStore, PokemonEvent, SqliteActivityStore
from ninja_bot.activity.tracker import Close, Move, Nothing, Open, plan_transition
from ninja_bot.cogs.cs2 import MemberCache, member_lookup
from ninja_bot.cs2.channels import PostChannel
from ninja_bot.cs2.formatting import escape_name
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.posting import PostChannelResolver, send_safely

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60.0
# How often the monthly auto-post checks for an ended, unposted month.
REPORT_CHECK_SECONDS = 3600.0
COOLDOWN_SECONDS = 10.0
# Longer than any `YYYY-MM` (with spaces); longer input is rejected by Discord.
MAX_MONTH_LENGTH = 20
GENERIC_ERROR_MESSAGE = "Something went wrong while loading voice activity. Please try again later."
POST_WARNING = "Not posting the monthly activity overview: the ACTIVITY_CHANNEL_ID channel is %s"


def cooldown_message(retry_after: float) -> str:
    """User-facing reply for the activity cooldown, rounded up to whole seconds."""
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return f"Slow down! You can use activity commands again in {seconds} {unit}."


def no_activity_message(name: str | None, month: Month) -> str:
    """Reply for a member without voice time; `name` is None for the caller."""
    label = month_label(month.year, month.month)
    if name is None:
        return f"You weren't in voice in {label}."
    return f"**{escape_name(name)}** wasn't in voice in {label}."


def _send_safely(send: Awaitable[object]) -> Awaitable[bool]:
    return send_safely(send, "activity response")


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
        timezone: str = "UTC",
        cooldown: UserCooldown | None = None,
        channel_id: int | None = None,
        members: MemberCache | None = None,
    ) -> None:
        self.bot = bot
        self.store = store
        self.guild_id = guild_id
        self.clock = clock
        # Months, days and hours are local to ACTIVITY_TIMEZONE (validated by config).
        self.tz = ZoneInfo(timezone)
        # Shared by /activity and /myactivity; refunded unless an embed with data was posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(COOLDOWN_SECONDS)
        self._reconciled = False
        # Monthly auto-post (ACTIVITY_CHANNEL_ID); off when None.
        self.channel_id = channel_id
        self.reporter = MonthlyReporter(store, self.tz, clock)
        # Finds the auto-post channel; a broken setup warns once instead of every hour.
        self._post_channel = (
            PostChannelResolver(bot, POST_WARNING, channel_id) if channel_id is not None else None
        )
        # Guild membership answers for the wall of shame (REST-backed, no members intent).
        self.members = members if members is not None else MemberCache()

    async def cog_load(self) -> None:
        if self.channel_id is None:
            log.debug("ACTIVITY_CHANNEL_ID is not set; monthly activity posts are off")
            return
        self.report_loop.start()

    async def cog_unload(self) -> None:
        self.heartbeat_loop.cancel()
        self.report_loop.cancel()

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

    @tasks.loop(seconds=REPORT_CHECK_SECONDS)
    async def report_loop(self) -> None:
        await self.run_report_cycle()

    @report_loop.before_loop
    async def _before_report_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def run_report_cycle(self) -> None:
        """Post at most one ended month's overview; never raises, so the loop keeps running."""
        if self._post_channel is None:
            return
        try:
            month = await self.reporter.due_month()
            if month is None:
                return
            channel = await self._post_channel.resolve()
            if channel is None:
                return
            await self._post_report(channel, month)
        except Exception:
            log.exception("Unexpected error in a monthly activity post cycle")

    async def _post_report(self, channel: PostChannel, month: Month) -> None:
        """Send one overview; confirmed after any send attempt, retried if building fails."""
        try:
            report = await self.monthly_report(month, channel.guild)
            embed = build_monthly_embed(report)
        except Exception:
            log.exception("Could not build the activity overview for %s; retrying", month.key)
            return
        try:
            # Leaderboard lines are mentions; render them, but never ping anyone.
            sent = await _send_safely(
                channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            )
            if sent:
                log.info("Posted the activity overview for %s", month.key)
        finally:
            await self.reporter.confirm(month)

    @app_commands.command(name="activity", description="Show the server's voice activity")
    @app_commands.describe(month="Which month, as YYYY-MM (defaults to this month)")
    async def activity(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[str, 1, MAX_MONTH_LENGTH] | None = None,
    ) -> None:
        await self.handle_activity(interaction, month)

    @app_commands.command(name="myactivity", description="Show a member's voice activity")
    @app_commands.describe(
        user="Whose activity to show (defaults to you)",
        month="Which month, as YYYY-MM (defaults to this month)",
    )
    async def myactivity(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        month: app_commands.Range[str, 1, MAX_MONTH_LENGTH] | None = None,
    ) -> None:
        await self.handle_myactivity(interaction, user, month)

    async def handle_activity(self, interaction: discord.Interaction, month: str | None) -> None:
        try:
            target = resolve_month(month, self.tz, self.clock())
        except InvalidMonthError as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return
        await self._run_with_cooldown(
            interaction, lambda: self._activity_reply(interaction, target)
        )

    async def handle_myactivity(
        self,
        interaction: discord.Interaction,
        user: discord.User | discord.Member | None,
        month: str | None,
    ) -> None:
        try:
            target = resolve_month(month, self.tz, self.clock())
        except InvalidMonthError as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return
        member = user if user is not None else interaction.user
        await self._run_with_cooldown(
            interaction, lambda: self._profile_reply(interaction, member, target)
        )

    async def _run_with_cooldown(
        self, interaction: discord.Interaction, work: Callable[[], Awaitable[bool]]
    ) -> None:
        """Take the caller's cooldown and run `work`; refund unless it posted.

        No defer: the store is local and fast, and replying directly lets "no data"
        errors stay private while embeds are public.
        """
        user_id = interaction.user.id
        retry_after = self.cooldown.try_acquire(user_id)
        if retry_after > 0:
            await interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            return
        posted = False
        try:
            posted = await work()
        finally:
            if not posted:
                self.cooldown.refund(user_id)

    async def _activity_reply(self, interaction: discord.Interaction, month: Month) -> bool:
        """Post the monthly overview; True only if it listed anyone."""
        try:
            report = await self.monthly_report(month, interaction.guild)
            embed = build_monthly_embed(report)
        except Exception:
            log.exception("Unexpected error building the activity overview")
            await _send_safely(
                interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            )
            return False
        sent = await _send_safely(
            interaction.response.send_message(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        )
        # The empty-month embed is a valid reply, but it shouldn't cost the cooldown.
        return sent and (bool(report.leaderboard) or report.pokemon.has_data)

    async def _profile_reply(
        self,
        interaction: discord.Interaction,
        member: discord.User | discord.Member,
        month: Month,
    ) -> bool:
        """Post one member's month; True only if the embed was posted."""
        try:
            report = await self.monthly_report(month)
            profile = user_profile(report, member.id, member.display_name)
            if profile is None:
                own = member.id == interaction.user.id
                message = no_activity_message(None if own else member.display_name, month)
                await _send_safely(interaction.response.send_message(message, ephemeral=True))
                return False
            embed = build_profile_embed(profile)
        except Exception:
            log.exception("Unexpected error building an activity profile")
            await _send_safely(
                interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            )
            return False
        return await _send_safely(
            interaction.response.send_message(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        )

    async def monthly_report(
        self, month: Month, guild: discord.Guild | None = None
    ) -> MonthlyReport:
        """Load `month` (sessions and Pokémon events) and the month before; build the report.

        With a `guild`, members who left it are dropped from the wall of shame.
        """
        now = self.clock()
        previous = month.previous()
        start = month.start(self.tz).timestamp()
        end = month.end(self.tz).timestamp()
        sessions = await self.store.sessions_between(start, end)
        previous_sessions = await self.store.sessions_between(
            previous.start(self.tz).timestamp(), start
        )
        pokemon_events = await self.store.pokemon_events_between(start, end)
        voice_user_ranges = await self.store.voice_user_ranges()
        report = build_monthly_report(
            sessions, previous_sessions, voice_user_ranges, month, self.tz, now, pokemon_events
        )
        if guild is None:
            return report
        return without_departed(report, await self.departed_members(guild, report))

    async def departed_members(self, guild: discord.Guild, report: MonthlyReport) -> set[int]:
        """Shame candidates Discord says are not in `guild`, checked lowest first.

        Stops once SHAME_LIMIT candidates are kept. Lookup errors keep the candidate.
        """
        lookup = member_lookup(guild, self.members)
        departed: set[int] = set()
        kept = 0
        for user_id, _ in report.shame_pool:
            if kept >= SHAME_LIMIT:
                break
            name = await lookup(user_id)
            # member_lookup only caches "not a member" for a definite 404.
            if name is None and self.members.not_members.get((guild.id, user_id)):
                departed.add(user_id)
            else:
                kept += 1
        return departed

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Activity cog", exc_info=error)
        if interaction.response.is_done():
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE, ephemeral=True))
        else:
            await _send_safely(
                interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            )


class PokemonTracker(commands.Cog):
    """Records Toasty's replies to `;pokemon`; only added when `TOASTY_BOT_ID` is set.

    Only parsed fields are stored, never message text.
    """

    def __init__(
        self,
        store: ActivityStore,
        toasty_bot_id: int,
        guild_id: int | None = None,
        pending: PendingCommands | None = None,
    ) -> None:
        self.store = store
        self.toasty_bot_id = toasty_bot_id
        self.guild_id = guild_id
        self.pending = pending if pending is not None else PendingCommands()

    def _in_scope(self, message: discord.Message) -> bool:
        if self.guild_id is None:
            return True
        return message.guild is not None and message.guild.id == self.guild_id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self._in_scope(message):
            return
        if message.author.id == self.toasty_bot_id:
            await self.record_reply(message)
        elif not message.author.bot and is_pokemon_command(message.content):
            self.pending.remember(message.id, message.author.id)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        # Recording is idempotent per reply id, so a re-parse never double counts.
        if self._in_scope(after) and after.author.id == self.toasty_bot_id:
            await self.record_reply(after)

    async def record_reply(self, message: discord.Message) -> None:
        """Record a Toasty reply to a remembered `;pokemon` command; ignore anything else."""
        reference = message.reference
        if reference is None or reference.message_id is None:
            return
        user_id = self.pending.author_of(reference.message_id)
        if user_id is None:
            return
        try:
            result = parse_reply(message.content, [embed.to_dict() for embed in message.embeds])
            if result is None:
                return
            ts = message.created_at.timestamp()
            if isinstance(result, Catch):
                event = PokemonEvent(message.id, user_id, ts, "catch", result.pokemon, result.shiny)
            else:
                event = PokemonEvent(message.id, user_id, ts, "cooldown")
            await self.store.record_pokemon_event(event)
        except Exception:
            log.exception("Failed to record a Toasty reply")


async def setup(bot: NinjaBot) -> None:
    store = SqliteActivityStore(Path(bot.config.data_dir) / "activity.sqlite3")
    await bot.add_cog(
        Activity(
            bot,
            store,
            bot.config.guild_id,
            timezone=bot.config.activity_timezone,
            cooldown=UserCooldown(COOLDOWN_SECONDS),
            channel_id=bot.config.activity_channel_id,
        )
    )
    if bot.config.toasty_bot_id is not None:
        await bot.add_cog(PokemonTracker(store, bot.config.toasty_bot_id, bot.config.guild_id))
