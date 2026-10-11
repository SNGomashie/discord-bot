from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from ninja_bot.cs2.channels import channel_problem
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.posting import send_safely
from ninja_bot.sessions import content
from ninja_bot.sessions.embed import build_poll_embed, format_slot_label
from ninja_bot.sessions.scoring import MAX_PLAYERS, Vote, pending_players, score_slots
from ninja_bot.sessions.service import (
    JOIN_MESSAGES,
    MAX_DURATION_HOURS,
    MAX_NAME_LENGTH,
    MAX_ROUND_HOURS,
    VOTE_MESSAGES,
    CloseReason,
    JoinResult,
    NoPrefill,
    SessionService,
    VoteResult,
    should_auto_close,
)
from ninja_bot.sessions.slots import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_WEEKS,
    MAX_DAYS_LENGTH,
    MAX_TIMES_LENGTH,
    MAX_WEEKS,
    InvalidSessionInput,
)
from ninja_bot.sessions.store import STATUS_VOTING, Poll, SqliteSessionStore
from ninja_bot.sessions.views import PollAction, PollButton, VoteView, poll_view

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

POLL_COOLDOWN_SECONDS = 30.0
# Longer than any `YYYY-MM-DD` (with spaces); longer input is rejected by Discord.
MAX_START_LENGTH = 20


def cooldown_message(retry_after: float) -> str:
    """User-facing reply for the /session_poll cooldown, rounded up to whole seconds."""
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return content.POLL_COOLDOWN_MESSAGE.format(seconds=seconds, unit=unit)


def _send_safely(send: Awaitable[object]) -> Awaitable[bool]:
    return send_safely(send, "session poll response")


class Sessions(commands.Cog):
    """Session polls; all rules live in `ninja_bot.sessions` (`SessionService`)."""

    def __init__(
        self,
        bot: commands.Bot,
        service: SessionService,
        cooldown: UserCooldown | None = None,
    ) -> None:
        self.bot = bot
        self.service = service
        # /session_poll; refunded unless a poll message was posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(POLL_COOLDOWN_SECONDS)

    async def cog_load(self) -> None:
        # Poll buttons carry their state in the custom_id, so they survive restarts.
        self.bot.add_dynamic_items(PollButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(PollButton)

    @app_commands.command(
        name="session_poll", description="Start a poll to find a date for a game session"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        name="What the session is, e.g. the campaign name",
        days="Weekdays to offer, e.g. fri,sat or weekend",
        times="Start times (24-hour), e.g. 19:00,14:00",
        weeks=f"How many weeks to offer (1-{MAX_WEEKS}, default {DEFAULT_WEEKS})",
        start="First day to offer, as YYYY-MM-DD (default tomorrow)",
        duration_hours=(
            f"How long a session lasts in hours (1-{MAX_DURATION_HOURS},"
            f" default {DEFAULT_DURATION_HOURS})"
        ),
        min_players=f"Players needed for a date to fit (2-{MAX_PLAYERS}, default everyone)",
        round_hours=f"Close each round after this many hours (1-{MAX_ROUND_HOURS}, optional)",
    )
    async def session_poll(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, MAX_NAME_LENGTH],
        days: app_commands.Range[str, 1, MAX_DAYS_LENGTH],
        times: app_commands.Range[str, 1, MAX_TIMES_LENGTH],
        weeks: app_commands.Range[int, 1, MAX_WEEKS] = DEFAULT_WEEKS,
        start: app_commands.Range[str, 1, MAX_START_LENGTH] | None = None,
        duration_hours: app_commands.Range[int, 1, MAX_DURATION_HOURS] = DEFAULT_DURATION_HOURS,
        min_players: app_commands.Range[int, 2, MAX_PLAYERS] | None = None,
        round_hours: app_commands.Range[int, 1, MAX_ROUND_HOURS] | None = None,
    ) -> None:
        await self.handle_session_poll(
            interaction,
            name,
            days,
            times,
            weeks,
            start,
            duration_hours,
            min_players,
            round_hours,
        )

    async def handle_session_poll(
        self,
        interaction: discord.Interaction,
        name: str,
        days: str,
        times: str,
        weeks: int = DEFAULT_WEEKS,
        start: str | None = None,
        duration_hours: int = DEFAULT_DURATION_HOURS,
        min_players: int | None = None,
        round_hours: int | None = None,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            await _send_safely(
                interaction.response.send_message(content.GUILD_ONLY_MESSAGE, ephemeral=True)
            )
            return
        # Results, reminders and edits are posted in this channel later, not as replies.
        if channel_problem(channel, guild.me) is not None:
            await _send_safely(
                interaction.response.send_message(content.CHANNEL_PROBLEM_MESSAGE, ephemeral=True)
            )
            return
        user_id = interaction.user.id
        retry_after = self.cooldown.try_acquire(user_id)
        if retry_after > 0:
            await _send_safely(
                interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            )
            return
        posted = False
        try:
            poll = await self._create_poll(
                interaction,
                guild.id,
                channel.id,
                name=name,
                days=days,
                times=times,
                weeks=weeks,
                start=start or "",
                duration_hours=duration_hours,
                min_players=min_players,
                round_hours=round_hours,
            )
            if poll is not None:
                posted = await self._post_poll(interaction, poll)
        finally:
            if not posted:
                self.cooldown.refund(user_id)

    async def _create_poll(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        channel_id: int,
        *,
        name: str,
        days: str,
        times: str,
        weeks: int,
        start: str,
        duration_hours: int,
        min_players: int | None,
        round_hours: int | None,
    ) -> Poll | None:
        """The new poll, or None after replying with the (ephemeral) reason."""
        try:
            return await self.service.create_poll(
                guild_id,
                channel_id,
                interaction.user.id,
                name,
                days,
                times,
                weeks=weeks,
                start_text=start,
                duration_hours=duration_hours,
                min_players=min_players,
                round_hours=round_hours,
            )
        except InvalidSessionInput as exc:
            await _send_safely(interaction.response.send_message(exc.message, ephemeral=True))
        except Exception:
            log.exception("Unexpected error creating a session poll")
            await _send_safely(
                interaction.response.send_message(content.GENERIC_ERROR_MESSAGE, ephemeral=True)
            )
        return None

    async def _post_poll(self, interaction: discord.Interaction, poll: Poll) -> bool:
        """Post the poll message and remember its id; a poll that wasn't posted is cancelled."""
        try:
            embed, view = await self.poll_message(poll)
        except Exception:
            log.exception("Unexpected error rendering session poll %d", poll.id)
            await _send_safely(
                interaction.response.send_message(content.GENERIC_ERROR_MESSAGE, ephemeral=True)
            )
            await self._discard(poll)
            return False
        try:
            response = await interaction.response.send_message(
                embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            log.warning("Could not post session poll %d", poll.id, exc_info=True)
            await self._discard(poll)
            return False
        try:
            message_id = response.message_id
            if message_id is None:
                message_id = (await interaction.original_response()).id
            await self.service.store.set_poll_message(poll.id, message_id)
        except Exception:
            # The poll works without it; only links and later edits of the message need it.
            log.exception("Could not store the message id of session poll %d", poll.id)
        return True

    async def _discard(self, poll: Poll) -> None:
        """Cancel a poll nobody can see, so it doesn't count towards the open-poll limit."""
        try:
            await self.service.cancel_poll(poll.id, poll.gm_id, is_manager=True)
        except Exception:
            log.exception("Could not cancel unposted session poll %d", poll.id)

    async def poll_message(self, poll: Poll) -> tuple[discord.Embed, discord.ui.View]:
        """The poll embed for the current round and its buttons."""
        players, slots, votes = await self.service.round_state(poll)
        slot_ids = [slot.id for slot in slots]
        embed = build_poll_embed(
            poll,
            slots,
            score_slots(slot_ids, players, votes),
            players,
            pending_players(players, slot_ids, votes),
        )
        return embed, poll_view(poll.id)

    async def handle_poll_action(
        self, interaction: discord.Interaction, poll_id: int, action: str, arg: int | None
    ) -> None:
        """Entry point for every poll button (see `sessions/views.py`)."""
        try:
            match action:
                case PollAction.JOIN:
                    await self.handle_join(interaction, poll_id)
                case PollAction.LEAVE:
                    await self.handle_leave(interaction, poll_id)
                case PollAction.VOTE:
                    await self.handle_vote(interaction, poll_id)
                case _:
                    await _send_safely(
                        interaction.response.send_message(
                            content.STALE_BUTTON_MESSAGE, ephemeral=True
                        )
                    )
        except Exception:
            log.exception("Unexpected error handling session poll button %r", action)
            await self._reply_error(interaction)

    async def handle_join(self, interaction: discord.Interaction, poll_id: int) -> None:
        result = await self.service.join(poll_id, interaction.user.id)
        await self._membership_reply(interaction, poll_id, result, JoinResult.JOINED)

    async def handle_leave(self, interaction: discord.Interaction, poll_id: int) -> None:
        result = await self.service.leave(poll_id, interaction.user.id)
        await self._membership_reply(interaction, poll_id, result, JoinResult.LEFT)

    async def _membership_reply(
        self,
        interaction: discord.Interaction,
        poll_id: int,
        result: JoinResult,
        changed: JoinResult,
    ) -> None:
        """Refusals are ephemeral; a change re-renders the poll, then confirms privately."""
        message = JOIN_MESSAGES[result]
        poll = await self.service.store.get_poll(poll_id) if result is changed else None
        if poll is None:
            await _send_safely(interaction.response.send_message(message, ephemeral=True))
            return
        embed, view = await self.poll_message(poll)
        await _send_safely(
            interaction.response.edit_message(
                embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
            )
        )
        await _send_safely(interaction.followup.send(message, ephemeral=True))

    async def handle_vote(self, interaction: discord.Interaction, poll_id: int) -> None:
        """Open the private vote view, pre-selected with the user's votes this round."""
        user_id = interaction.user.id
        store = self.service.store
        poll = await store.get_poll(poll_id)
        if poll is None or poll.status != STATUS_VOTING:
            await _send_safely(
                interaction.response.send_message(content.POLL_CLOSED_MESSAGE, ephemeral=True)
            )
            return
        if user_id not in await store.players(poll_id):
            await _send_safely(
                interaction.response.send_message(content.NOT_JOINED_MESSAGE, ephemeral=True)
            )
            return
        slots = await store.poll_slots(poll_id, poll.round_number)
        votes = await store.votes(poll_id, poll.round_number)
        mine = {slot_id: vote for (voter, slot_id), vote in votes.items() if voter == user_id}

        async def save(
            save_interaction: discord.Interaction, yes_ids: list[int], maybe_ids: list[int]
        ) -> None:
            await self.handle_vote_save(save_interaction, poll_id, yes_ids, maybe_ids)

        view = VoteView(
            [(slot.id, format_slot_label(slot, self.service.tz)) for slot in slots],
            [slot_id for slot_id, vote in mine.items() if vote is Vote.YES],
            [slot_id for slot_id, vote in mine.items() if vote is Vote.IF_NEEDED],
            save,
        )
        await _send_safely(
            interaction.response.send_message(
                content.VOTE_PROMPT.format(tz=self.service.tz.key), view=view, ephemeral=True
            )
        )

    async def handle_vote_save(
        self,
        interaction: discord.Interaction,
        poll_id: int,
        yes_ids: list[int],
        maybe_ids: list[int],
    ) -> None:
        """Save from the vote view: store the votes, confirm privately, refresh the poll."""
        try:
            result = await self.service.submit_vote(
                poll_id, interaction.user.id, yes_ids, maybe_ids
            )
        except Exception:
            log.exception("Unexpected error saving a session poll vote")
            await self._reply_error(interaction)
            return
        await _send_safely(
            interaction.response.edit_message(content=VOTE_MESSAGES[result], view=None)
        )
        if result is not VoteResult.RECORDED:
            return
        await self.refresh_poll_message(poll_id)
        await self.advance_if_ready(poll_id)

    async def refresh_poll_message(self, poll_id: int) -> None:
        """Re-render the public poll message; failures are logged, never raised."""
        try:
            poll = await self.service.store.get_poll(poll_id)
            if poll is None or poll.message_id is None:
                return
            embed, view = await self.poll_message(poll)
            message = self.bot.get_partial_messageable(poll.channel_id).get_partial_message(
                poll.message_id
            )
            await send_safely(
                message.edit(
                    embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
                ),
                "session poll refresh",
            )
        except Exception:
            log.exception("Could not refresh session poll %d", poll_id)

    async def advance_if_ready(self, poll_id: int) -> CloseReason | None:
        """Why the poll's round should close after a vote (`"all_voted"`), or None.

        Deadlines are left to the background loop; errors are logged and give None.
        """
        try:
            poll = await self.service.store.get_poll(poll_id)
            if poll is None:
                return None
            players, slots, votes = await self.service.round_state(poll)
            reason = should_auto_close(
                poll, players, [slot.id for slot in slots], votes, self.service.clock()
            )
        except Exception:
            log.exception("Could not check whether session poll %d is ready", poll_id)
            return None
        return reason if reason == "all_voted" else None

    async def _reply_error(self, interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            await _send_safely(
                interaction.followup.send(content.GENERIC_ERROR_MESSAGE, ephemeral=True)
            )
        else:
            await _send_safely(
                interaction.response.send_message(content.GENERIC_ERROR_MESSAGE, ephemeral=True)
            )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Sessions cog", exc_info=error)
        await self._reply_error(interaction)


async def setup(bot: NinjaBot) -> None:
    store = SqliteSessionStore(Path(bot.config.data_dir) / "sessions.sqlite3")
    service = SessionService(store, ZoneInfo(bot.config.sessions_timezone), time.time, NoPrefill())
    await bot.add_cog(Sessions(bot, service, UserCooldown(POLL_COOLDOWN_SECONDS)))
