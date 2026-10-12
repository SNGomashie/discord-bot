from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ninja_bot.cs2.channels import PostChannel, channel_problem
from ninja_bot.cs2.formatting import escape_name
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.posting import PostChannelResolver, send_safely
from ninja_bot.sessions import content
from ninja_bot.sessions.cancel import cancel_choices, parse_cancel_item
from ninja_bot.sessions.embed import (
    build_list_embed,
    build_next_round_embed,
    build_no_fit_embed,
    build_poll_embed,
    build_result_embed,
    build_session_embed,
    format_slot_label,
    mention,
    timestamp,
)
from ninja_bot.sessions.players import MAX_PLAYERS_TEXT_LENGTH
from ninja_bot.sessions.reminders import SessionNotifier
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
    NotAllowed,
    NotReady,
    NotVoting,
    RoundAdvanced,
    RoundFailed,
    RoundFits,
    RoundOutcome,
    SessionRefused,
    SessionService,
    VoteResult,
    should_auto_close,
)
from ninja_bot.sessions.slots import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_WEEKS,
    MAX_DAYS_LENGTH,
    MAX_EXCLUDE_LENGTH,
    MAX_TIMES_LENGTH,
    MAX_WEEKS,
    InvalidSessionInput,
)
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_VOTING,
    Poll,
    RoundState,
    Session,
    SqliteSessionStore,
)
from ninja_bot.sessions.views import PollAction, PollButton, VoteView, confirm_view, poll_view

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

POLL_COOLDOWN_SECONDS = 30.0
LIST_COOLDOWN_SECONDS = 10.0
# Longer than any `YYYY-MM-DD` (with spaces); longer input is rejected by Discord.
MAX_START_LENGTH = 20
# How often the background loop closes due rounds and sends reminders and nudges.
NOTIFY_CHECK_SECONDS = 60
# `{channel_id}` is filled in per channel; `%s` is the channel problem.
POST_WARNING = "Can't post session poll messages in channel {channel_id}: %s"


def cooldown_message(retry_after: float, template: str = content.POLL_COOLDOWN_MESSAGE) -> str:
    """User-facing cooldown reply (/session_poll by default), rounded up to whole seconds."""
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return template.format(seconds=seconds, unit=unit)


def _send_safely(send: Awaitable[object]) -> Awaitable[bool]:
    return send_safely(send, "session poll response")


def _pinging(user_ids: Sequence[int]) -> discord.AllowedMentions:
    """Mentions that ping exactly these users (and nobody else)."""
    return discord.AllowedMentions(
        everyone=False,
        roles=False,
        replied_user=False,
        users=[discord.Object(id=user_id) for user_id in user_ids],
    )


def _is_manager(interaction: discord.Interaction) -> bool:
    return bool(interaction.permissions.manage_events)


class Sessions(commands.Cog):
    """Session polls; all rules live in `ninja_bot.sessions` (`SessionService`)."""

    def __init__(
        self,
        bot: commands.Bot,
        service: SessionService,
        cooldown: UserCooldown | None = None,
        notifier: SessionNotifier | None = None,
        list_cooldown: UserCooldown | None = None,
    ) -> None:
        self.bot = bot
        self.service = service
        # /session_poll; refunded unless a poll message was posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(POLL_COOLDOWN_SECONDS)
        # /sessions; refunded unless the list was posted.
        self.list_cooldown = (
            list_cooldown if list_cooldown is not None else UserCooldown(LIST_COOLDOWN_SECONDS)
        )
        self.notifier = (
            notifier if notifier is not None else SessionNotifier(service.store, service.clock)
        )
        # One resolver per poll/session channel, so a broken channel warns once per cause.
        self._channels: dict[int, PostChannelResolver] = {}
        # Held around closing a round and posting its outcome, and around a repair, so a
        # repair never sees a closed round whose outcome is still being posted.
        self._outcome_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        # Poll buttons carry their state in the custom_id, so they survive restarts.
        self.bot.add_dynamic_items(PollButton)
        self.notify_loop.start()

    async def cog_unload(self) -> None:
        self.notify_loop.cancel()
        self.bot.remove_dynamic_items(PollButton)

    @tasks.loop(seconds=NOTIFY_CHECK_SECONDS)
    async def notify_loop(self) -> None:
        await self.run_notify_cycle()

    @notify_loop.before_loop
    async def _before_notify_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def run_notify_cycle(self) -> None:
        """Repair unposted outcomes, close due rounds, then post reminders and nudges; never
        raises."""
        # Each voting poll's round is read once (by the close step) and reused for nudges.
        states: dict[tuple[int, int], RoundState] = {}
        steps: tuple[Callable[[], Awaitable[None]], ...] = (
            self.repair_missing_messages,
            lambda: self._close_due_rounds(states),
            self._post_reminders,
            lambda: self._post_nudges(states),
        )
        for step in steps:
            try:
                await step()
            except Exception:
                log.exception("Unexpected error in a session poll notify cycle")

    async def _channel(self, channel_id: int) -> PostChannel | None:
        """The usable channel, or None (warned once per cause) to skip the post this cycle."""
        resolver = self._channels.get(channel_id)
        if resolver is None:
            resolver = PostChannelResolver(
                self.bot, POST_WARNING.format(channel_id=channel_id), channel_id
            )
            self._channels[channel_id] = resolver
        return await resolver.resolve()

    async def repair_missing_messages(self) -> None:
        """Post again the result or next-round message Discord rejected; never raises."""
        for poll in await self.service.store.polls_missing_messages():
            try:
                async with self._outcome_lock:
                    await self._repair(poll.id)
            except Exception:
                log.exception("Could not repair the message of session poll %d", poll.id)

    async def _repair(self, poll_id: int) -> None:
        """Re-read the poll (under `_outcome_lock`) and post its outcome if still missing."""
        poll = await self.service.store.get_poll(poll_id)
        if poll is None:
            return
        if poll.status == STATUS_VOTING and poll.message_id is not None:
            return
        if poll.status == STATUS_AWAITING_CONFIRM and poll.result_message_id is not None:
            return
        if await self._channel(poll.channel_id) is None:
            return
        outcome = await self.service.rebuild_outcome(poll_id)
        if outcome is not None:
            log.info("Posting the missing outcome message of session poll %d", poll_id)
            await self.post_outcome(outcome)

    async def _close_due_rounds(self, states: dict[tuple[int, int], RoundState]) -> None:
        """Close rounds past their deadline (or fully voted) and post the outcome.

        Fills `states` with the round state of every poll it leaves open, for the nudges;
        a round it tries to close is left out, so it isn't nudged this cycle.
        """
        now = self.service.clock()
        for poll in await self.service.store.voting_polls():
            try:
                state = await self.service.round_state(poll)
                players, slots, votes = state
                reason = should_auto_close(poll, players, [slot.id for slot in slots], votes, now)
                # A broken channel leaves the round open until the outcome can be posted.
                if reason is None or await self._channel(poll.channel_id) is None:
                    states[(poll.id, poll.round_number)] = state
                    continue
                await self.close_and_post(poll.id, reason)
            except Exception:
                log.exception("Could not auto-close the round of session poll %d", poll.id)

    async def _post_reminders(self) -> None:
        """Ping the roster before a session; marked sent after every send attempt."""
        for session, offset in await self.notifier.due_reminders():
            channel = await self._channel(session.channel_id)
            if channel is None:
                continue
            roster = list(session.roster)
            try:
                await send_safely(
                    channel.send(
                        content=content.REMINDER_MESSAGE.format(
                            players=", ".join(mention(user_id) for user_id in roster),
                            name=escape_name(session.name, MAX_NAME_LENGTH),
                            start=timestamp(session.start, "R"),
                        ),
                        embed=build_session_embed(session),
                        allowed_mentions=_pinging(roster),
                    ),
                    "session reminder",
                )
            finally:
                await self.notifier.confirm_reminder(session.id, offset)

    async def _post_nudges(self, states: Mapping[tuple[int, int], RoundState]) -> None:
        """Reply to the poll message pinging who hasn't voted; counted after every attempt."""
        for poll, pending in await self.notifier.due_nudges(states):
            # Without a poll message (its post failed and awaits repair) there's nothing to
            # vote on yet.
            if poll.message_id is None:
                continue
            channel = await self._channel(poll.channel_id)
            if channel is None:
                continue
            # Still sent (as a plain message) if the poll message was deleted.
            reference = discord.MessageReference(
                message_id=poll.message_id,
                channel_id=poll.channel_id,
                guild_id=poll.guild_id,
                fail_if_not_exists=False,
            )
            try:
                await send_safely(
                    channel.send(
                        content=content.NUDGE_MESSAGE.format(
                            players=", ".join(mention(user_id) for user_id in pending),
                            name=escape_name(poll.name, MAX_NAME_LENGTH),
                            round=poll.round_number,
                        ),
                        allowed_mentions=_pinging(pending),
                        reference=reference,
                    ),
                    "session poll nudge",
                )
            finally:
                await self.notifier.confirm_nudge(poll.id, poll.round_number)

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
        exclude="Days to skip: dates or ranges, e.g. 2026-11-05,2026-11-13..2026-11-15",
        players="Mention the players to fix the list (then nobody can join or leave)",
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
        exclude: app_commands.Range[str, 1, MAX_EXCLUDE_LENGTH] | None = None,
        players: app_commands.Range[str, 1, MAX_PLAYERS_TEXT_LENGTH] | None = None,
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
            exclude,
            players,
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
        exclude: str | None = None,
        players: str | None = None,
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
                exclude=exclude or "",
                players=players or "",
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
        exclude: str,
        players: str,
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
                exclude_text=exclude,
                players_text=players,
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
            invited = await self._invited(poll)
            response = await interaction.response.send_message(
                content=content.POLL_INVITE_PING.format(
                    players=" ".join(mention(user_id) for user_id in invited)
                )
                if invited
                else None,
                embed=embed,
                view=view,
                allowed_mentions=_pinging(invited) if invited else discord.AllowedMentions.none(),
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

    async def _invited(self, poll: Poll) -> list[int]:
        """The listed players to ping when the poll is posted (none without a fixed list)."""
        if not poll.fixed_roster:
            return []
        return [
            user_id
            for user_id in await self.service.store.players(poll.id)
            if user_id != poll.gm_id
        ]

    async def _discard(self, poll: Poll) -> None:
        """Cancel a poll nobody can see, so it doesn't count towards the open-poll limit."""
        try:
            await self.service.cancel_poll(poll.id, poll.gm_id, is_manager=True)
        except Exception:
            log.exception("Could not cancel unposted session poll %d", poll.id)

    async def poll_message(self, poll: Poll) -> tuple[discord.Embed, discord.ui.View]:
        """The poll embed for the current round and its buttons."""
        return await self.poll_embed(poll), poll_view(poll.id, poll.fixed_roster)

    async def poll_embed(self, poll: Poll) -> discord.Embed:
        """The poll embed for the current round."""
        players, slots, votes = await self.service.round_state(poll)
        slot_ids = [slot.id for slot in slots]
        return build_poll_embed(
            poll,
            slots,
            score_slots(slot_ids, players, votes),
            players,
            pending_players(players, slot_ids, votes),
        )

    @app_commands.command(
        name="sessions", description="List open session polls and upcoming sessions"
    )
    @app_commands.guild_only()
    async def sessions(self, interaction: discord.Interaction) -> None:
        await self.handle_sessions(interaction)

    async def handle_sessions(self, interaction: discord.Interaction) -> None:
        """Post the open polls and upcoming sessions of this server publicly."""
        guild = interaction.guild
        if guild is None:
            await _send_safely(
                interaction.response.send_message(content.GUILD_ONLY_MESSAGE, ephemeral=True)
            )
            return
        user_id = interaction.user.id
        retry_after = self.list_cooldown.try_acquire(user_id)
        if retry_after > 0:
            await _send_safely(
                interaction.response.send_message(
                    cooldown_message(retry_after, content.LIST_COOLDOWN_MESSAGE), ephemeral=True
                )
            )
            return
        posted = False
        try:
            overview = await self.service.overview(guild.id)
            embed = build_list_embed(overview.polls, overview.sessions, overview.pending)
            posted = await _send_safely(
                interaction.response.send_message(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none()
                )
            )
        except Exception:
            log.exception("Unexpected error listing session polls")
            await self._reply_error(interaction)
        finally:
            if not posted:
                self.list_cooldown.refund(user_id)

    @app_commands.command(
        name="session_cancel", description="Cancel a session poll or an upcoming session"
    )
    @app_commands.guild_only()
    @app_commands.describe(item="The open poll or upcoming session to cancel (pick from the list)")
    async def session_cancel(self, interaction: discord.Interaction, item: str) -> None:
        await self.handle_session_cancel(interaction, item)

    @session_cancel.autocomplete("item")
    async def _session_cancel_item(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in await self.cancel_choices(interaction, current)
        ]

    async def cancel_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[tuple[str, str]]:
        """(label, value) for this server's open polls and upcoming sessions matching
        `current`; errors give no choices."""
        guild = interaction.guild
        if guild is None:
            return []
        try:
            polls = await self.service.store.open_polls(guild.id)
            sessions = await self.service.store.upcoming_sessions(guild.id, self.service.clock())
        except Exception:
            log.exception("Could not list session poll items to cancel")
            return []
        return cancel_choices(polls, sessions, self.service.tz, current)

    async def handle_session_cancel(self, interaction: discord.Interaction, item: str) -> None:
        """Cancel a poll or session (GM or Manage Events), mark its message, confirm publicly."""
        guild = interaction.guild
        target = parse_cancel_item(item)
        if guild is None or target is None:
            message = (
                content.GUILD_ONLY_MESSAGE if guild is None else content.UNKNOWN_CANCEL_ITEM_MESSAGE
            )
            await _send_safely(interaction.response.send_message(message, ephemeral=True))
            return
        user_id = interaction.user.id
        try:
            if target.kind == "poll":
                poll = await self.service.cancel_poll(
                    target.id, user_id, _is_manager(interaction), guild.id
                )
                name, channel_id = poll.name, poll.channel_id
                message_ids = [poll.message_id, poll.result_message_id]
                edit = content.CANCELLED_POLL_EDIT
                reply = content.POLL_CANCELLED_MESSAGE.format(
                    name=escape_name(poll.name, MAX_NAME_LENGTH), user=mention(user_id)
                )
            else:
                session = await self.service.cancel_session(
                    target.id, user_id, _is_manager(interaction), guild.id
                )
                name, channel_id = session.name, session.channel_id
                message_ids = [session.message_id]
                edit = content.CANCELLED_SESSION_EDIT
                reply = content.SESSION_CANCELLED_MESSAGE.format(
                    name=escape_name(session.name, MAX_NAME_LENGTH),
                    start=timestamp(session.start, "F"),
                    user=mention(user_id),
                )
        except SessionRefused as exc:
            await _send_safely(interaction.response.send_message(exc.message, ephemeral=True))
            return
        log.info("Session %s %d (%s) cancelled by %d", target.kind, target.id, name, user_id)
        await _send_safely(
            interaction.response.send_message(
                reply, allowed_mentions=discord.AllowedMentions.none()
            )
        )
        # A poll's result message has Confirm buttons too.
        for message_id in message_ids:
            await self._edit_message(
                channel_id,
                message_id,
                content=edit,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )

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
                case PollAction.CLOSE:
                    await self.handle_close(interaction, poll_id)
                case PollAction.CONFIRM if arg is not None:
                    await self.handle_confirm(interaction, poll_id, arg)
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
            message = (
                content.NOT_ON_LIST_MESSAGE if poll.fixed_roster else content.NOT_JOINED_MESSAGE
            )
            await _send_safely(interaction.response.send_message(message, ephemeral=True))
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
        """Re-render the embed of a voting poll's message; failures are logged, never raised.

        Closed polls are left alone, and the edit never sends `view=`: the buttons are
        static, so a refresh that lands after a close can't bring them back.
        """
        try:
            poll = await self.service.store.get_poll(poll_id)
            if poll is None or poll.status != STATUS_VOTING or poll.message_id is None:
                return
            embed = await self.poll_embed(poll)
        except Exception:
            log.exception("Could not refresh session poll %d", poll_id)
            return
        await self._edit_poll_message(
            poll, embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    async def advance_if_ready(self, poll_id: int) -> RoundOutcome | None:
        """Close the round once everyone voted (`"all_voted"`) and post the outcome.

        None when the round stays open; deadlines are left to the background loop.
        Errors are logged and give None.
        """
        try:
            poll = await self.service.store.get_poll(poll_id)
            if poll is None:
                return None
            players, slots, votes = await self.service.round_state(poll)
            reason = should_auto_close(
                poll, players, [slot.id for slot in slots], votes, self.service.clock()
            )
            if reason != "all_voted":
                return None
            return await self.close_and_post(poll_id, reason)
        except Exception:
            log.exception("Could not close the round of session poll %d", poll_id)
            return None

    async def close_and_post(
        self,
        poll_id: int,
        reason: CloseReason,
        by_user_id: int = 0,
        is_manager: bool = False,
    ) -> RoundOutcome:
        """Close the round via the service and post its outcome in the poll's channel.

        `NotVoting` / `NotAllowed` / `NotReady` post nothing (a double click, a lost race or
        a round that changed since its reason was checked).
        """
        async with self._outcome_lock:
            outcome = await self.service.close_round(poll_id, by_user_id, is_manager, reason)
            if not isinstance(outcome, NotVoting | NotAllowed | NotReady):
                await self.post_outcome(outcome)
        return outcome

    async def post_outcome(self, outcome: RoundFits | RoundAdvanced | RoundFailed) -> None:
        """Mark the old poll message closed (buttons removed), then post the outcome."""
        poll = outcome.poll
        # A next round has already moved the poll on; the old message shows the round before.
        closed_round = poll.round_number - (1 if isinstance(outcome, RoundAdvanced) else 0)
        await self._edit_poll_message(
            poll,
            content=content.ROUND_CLOSED_MESSAGE.format(round=closed_round),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if isinstance(outcome, RoundFits):
            await self._post_result(outcome)
        elif isinstance(outcome, RoundAdvanced):
            await self._post_next_round(outcome)
        else:
            await self._post_no_fit(outcome)

    async def _post_result(self, outcome: RoundFits) -> None:
        poll = outcome.poll
        players, _, votes = await self.service.round_state(poll)
        tz = self.service.tz
        message = await self._post(
            poll.channel_id,
            "session poll result",
            content=content.RESULT_PING.format(gm=mention(poll.gm_id)),
            embed=build_result_embed(poll, outcome.top, outcome.slots, players, votes),
            view=confirm_view(poll.id, [(s.id, format_slot_label(s, tz)) for s in outcome.slots]),
            allowed_mentions=_pinging([poll.gm_id]),
        )
        if message is not None:
            await self.service.store.set_result_message(poll.id, message.id)

    async def _post_next_round(self, outcome: RoundAdvanced) -> None:
        poll = outcome.poll
        embed, view = await self.poll_message(poll)
        pending = list(outcome.pending)
        ping = ", ".join(mention(user_id) for user_id in pending)
        message = await self._post(
            poll.channel_id,
            "session poll round",
            content=content.NEXT_ROUND_PING.format(players=ping) if pending else None,
            embeds=[build_next_round_embed(poll, outcome.slots, pending), embed],
            view=view,
            allowed_mentions=_pinging(pending),
        )
        if message is not None:
            await self.service.store.set_poll_message(poll.id, message.id)

    async def _post_no_fit(self, outcome: RoundFailed) -> None:
        await self._post(
            outcome.poll.channel_id,
            "session poll failure",
            embed=build_no_fit_embed(outcome.poll, outcome.reason),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def handle_close(self, interaction: discord.Interaction, poll_id: int) -> None:
        """**Close round**: GM or Manage Events only; the outcome is posted in the channel."""
        async with self._outcome_lock:
            outcome = await self.service.close_round(
                poll_id, interaction.user.id, _is_manager(interaction), "manual"
            )
            if isinstance(outcome, NotAllowed):
                message = content.NOT_ALLOWED_MESSAGE
            elif isinstance(outcome, NotVoting | NotReady):
                # A manual close never gives `NotReady`; it is handled for completeness.
                message = (
                    content.STALE_BUTTON_MESSAGE
                    if outcome.poll is None
                    else content.ROUND_ALREADY_CLOSED_MESSAGE
                )
            else:
                await _send_safely(interaction.response.defer())
                await self.post_outcome(outcome)
                return
        await _send_safely(interaction.response.send_message(message, ephemeral=True))

    async def handle_confirm(
        self, interaction: discord.Interaction, poll_id: int, slot_id: int
    ) -> None:
        """**Confirm**: GM or Manage Events only; marks the result and announces the session."""
        try:
            session = await self.service.confirm(
                poll_id, slot_id, interaction.user.id, _is_manager(interaction)
            )
        except SessionRefused as exc:
            await _send_safely(interaction.response.send_message(exc.message, ephemeral=True))
            return
        await _send_safely(
            interaction.response.edit_message(
                content=content.CONFIRMED_RESULT_MESSAGE.format(
                    start=timestamp(session.start, "F")
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        )
        await self._announce_session(session)

    async def _announce_session(self, session: Session) -> None:
        roster = list(session.roster)
        await self._post(
            session.channel_id,
            "session announcement",
            content=content.SESSION_ANNOUNCEMENT.format(
                players=", ".join(mention(user_id) for user_id in roster)
            )
            if roster
            else None,
            embed=build_session_embed(session),
            allowed_mentions=_pinging(roster),
        )

    async def _post(self, channel_id: int, what: str, **kwargs: Any) -> discord.Message | None:
        """Send a message to a channel through `send_safely`; the message, or None."""
        sent: list[discord.Message] = []

        async def send() -> None:
            channel = self.bot.get_partial_messageable(channel_id)
            sent.append(await channel.send(**kwargs))

        await send_safely(send(), what)
        return sent[0] if sent else None

    async def _edit_poll_message(self, poll: Poll, **kwargs: Any) -> None:
        """Edit the poll's public message; failures are logged, never raised."""
        await self._edit_message(poll.channel_id, poll.message_id, **kwargs)

    async def _edit_message(self, channel_id: int, message_id: int | None, **kwargs: Any) -> None:
        """Edit a bot message (skipped without an id); failures are logged, never raised."""
        if message_id is None:
            return
        try:
            message = self.bot.get_partial_messageable(channel_id).get_partial_message(message_id)
            await send_safely(message.edit(**kwargs), "session poll edit")
        except Exception:
            log.exception("Could not edit session poll message %d", message_id)

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
