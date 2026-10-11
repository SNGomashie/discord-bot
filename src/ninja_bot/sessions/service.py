"""Session poll rules: create, join, vote, close a round, confirm, cancel (no discord import)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from ninja_bot.sessions import content
from ninja_bot.sessions.scoring import (
    MAX_PLAYERS,
    MAX_ROUNDS,
    Fits,
    NextRound,
    RoundResult,
    SlotScore,
    Vote,
    Votes,
    all_voted,
    evaluate_round,
    pending_players,
    score_slots,
)
from ninja_bot.sessions.slots import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_WEEKS,
    MAX_WEEKS,
    InvalidSessionInput,
    generate_slots,
    next_window_start,
    parse_days,
    parse_start_date,
    parse_times,
)
from ninja_bot.sessions.store import (
    OPEN_STATUSES,
    STATUS_AWAITING_CONFIRM,
    STATUS_FAILED,
    STATUS_VOTING,
    Poll,
    PollSlot,
    Session,
    SessionStore,
)

MAX_NAME_LENGTH = 60
MAX_OPEN_POLLS = 5
MAX_DURATION_HOURS = 12
MAX_ROUND_HOURS = 336


class JoinResult(Enum):
    JOINED = "joined"
    ALREADY_JOINED = "already_joined"
    FULL = "full"
    LEFT = "left"
    NOT_JOINED = "not_joined"
    POLL_CLOSED = "poll_closed"
    GM_CANNOT_LEAVE = "gm_cannot_leave"


class VoteResult(Enum):
    RECORDED = "recorded"
    NOT_JOINED = "not_joined"
    POLL_CLOSED = "poll_closed"
    UNKNOWN_SLOT = "unknown_slot"


JOIN_MESSAGES: dict[JoinResult, str] = {
    JoinResult.JOINED: content.JOINED_MESSAGE,
    JoinResult.ALREADY_JOINED: content.ALREADY_JOINED_MESSAGE,
    JoinResult.FULL: content.POLL_FULL_MESSAGE.format(max=MAX_PLAYERS),
    JoinResult.LEFT: content.LEFT_MESSAGE,
    JoinResult.NOT_JOINED: content.NOT_JOINED_MESSAGE,
    JoinResult.POLL_CLOSED: content.POLL_CLOSED_MESSAGE,
    JoinResult.GM_CANNOT_LEAVE: content.GM_CANNOT_LEAVE_MESSAGE,
}

VOTE_MESSAGES: dict[VoteResult, str] = {
    VoteResult.RECORDED: content.VOTE_RECORDED_MESSAGE,
    VoteResult.NOT_JOINED: content.NOT_JOINED_MESSAGE,
    VoteResult.POLL_CLOSED: content.POLL_CLOSED_MESSAGE,
    VoteResult.UNKNOWN_SLOT: content.UNKNOWN_SLOT_MESSAGE,
}


# Why a round is closed: by the GM/a manager, or automatically.
CloseReason = Literal["manual", "all_voted", "deadline"]


class SessionRefused(Exception):
    """A refused confirm/cancel; `.message` is the user-facing reason."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Overview:
    """What `/sessions` shows; `pending` is keyed by poll id (voting polls only)."""

    polls: list[Poll]
    sessions: list[Session]
    pending: dict[int, tuple[int, ...]]


@dataclass(frozen=True)
class RoundFits:
    """Slots fit; the poll is `awaiting_confirm`. `slots` matches `top` (best first)."""

    poll: Poll
    top: tuple[SlotScore, ...]
    slots: tuple[PollSlot, ...]
    pending: tuple[int, ...]


@dataclass(frozen=True)
class RoundAdvanced:
    """Nothing fit; `poll` is in its next round with the new `slots`.

    `pending` are the players who didn't vote in the closed round. `poll.message_id` is
    still the closed round's message (the store already cleared it until the new round's
    message is posted), so the cog can mark it closed.
    """

    poll: Poll
    slots: tuple[PollSlot, ...]
    pending: tuple[int, ...]


class FailReason(Enum):
    # Nothing fit in round `MAX_ROUNDS`.
    LAST_ROUND = "last_round"
    # Nothing fit and the next window has no usable slots.
    NO_SLOTS = "no_slots"


@dataclass(frozen=True)
class RoundFailed:
    """No date was found; the poll is `failed`."""

    poll: Poll
    reason: FailReason
    pending: tuple[int, ...]


@dataclass(frozen=True)
class NotVoting:
    """The poll is unknown or its round was already closed; nothing changed."""

    poll: Poll | None


@dataclass(frozen=True)
class NotAllowed:
    """A manual close by someone who is neither the GM nor a manager."""

    poll: Poll


@dataclass(frozen=True)
class NotReady:
    """An automatic close whose reason no longer holds (e.g. a player joined); the round
    stays open."""

    poll: Poll


RoundOutcome = RoundFits | RoundAdvanced | RoundFailed | NotVoting | NotAllowed | NotReady


def should_auto_close(
    poll: Poll, players: Sequence[int], slot_ids: Sequence[int], votes: Votes, now: float
) -> CloseReason | None:
    """Why the round should close on its own now, or None (only while `voting`)."""
    if poll.status != STATUS_VOTING:
        return None
    if all_voted(players, slot_ids, votes):
        return "all_voted"
    if poll.round_hours and poll.round_started_at + poll.round_hours * 3600 <= now:
        return "deadline"
    return None


def _may_manage(poll_gm_id: int, user_id: int, is_manager: bool) -> bool:
    return is_manager or user_id == poll_gm_id


def _evaluate(
    poll: Poll, players: Sequence[int], slot_ids: Sequence[int], votes: Votes
) -> RoundResult:
    scores = score_slots(slot_ids, players, votes)
    return evaluate_round(
        scores, players, poll.gm_id, votes, poll.min_players, poll.round_number, MAX_ROUNDS
    )


class Prefiller(Protocol):
    async def prefill(self, user_id: int, slots: Sequence[PollSlot]) -> dict[int, Vote]:
        """Suggested votes (slot id -> vote) for a player who just joined."""
        ...


class NoPrefill:
    async def prefill(self, user_id: int, slots: Sequence[PollSlot]) -> dict[int, Vote]:
        return {}


def _check_range(value: float, low: float, high: float, message: str) -> None:
    if not low <= value <= high:
        raise InvalidSessionInput(message.format(max=high))


class SessionService:
    """The poll rules; `min_players` / `round_hours` of 0 mean "not set"."""

    def __init__(
        self,
        store: SessionStore,
        tz: ZoneInfo,
        clock: Callable[[], float],
        prefiller: Prefiller,
    ) -> None:
        self.store = store
        self.tz = tz
        self.clock = clock
        self.prefiller = prefiller
        # Serialises joins, leaves, votes, closing, confirming and cancelling, so a vote is
        # either in the evaluated round or refused, and double clicks and the loop can't
        # race. Helpers called under it never take it again.
        self._lock = asyncio.Lock()

    async def create_poll(
        self,
        guild_id: int,
        channel_id: int,
        gm_id: int,
        name: str,
        days_text: str,
        times_text: str,
        weeks: int = DEFAULT_WEEKS,
        start_text: str = "",
        duration_hours: int = DEFAULT_DURATION_HOURS,
        min_players: int | None = None,
        round_hours: int | None = None,
    ) -> Poll:
        """Validate the input and store a new poll in round 1; raises `InvalidSessionInput`."""
        name = name.strip()
        if not name:
            raise InvalidSessionInput(content.BLANK_NAME_MESSAGE)
        if len(name) > MAX_NAME_LENGTH:
            raise InvalidSessionInput(content.NAME_TOO_LONG_MESSAGE.format(max=MAX_NAME_LENGTH))
        _check_range(weeks, 1, MAX_WEEKS, content.INVALID_WEEKS_MESSAGE)
        _check_range(duration_hours, 1, MAX_DURATION_HOURS, content.INVALID_DURATION_MESSAGE)
        if min_players is not None:
            _check_range(min_players, 2, MAX_PLAYERS, content.INVALID_MIN_PLAYERS_MESSAGE)
        if round_hours is not None:
            _check_range(round_hours, 1, MAX_ROUND_HOURS, content.INVALID_ROUND_HOURS_MESSAGE)
        days = parse_days(days_text)
        times = parse_times(times_text)
        now = self.clock()
        start = parse_start_date(start_text, self.tz, now)
        slots = generate_slots(start, weeks, days, times, self.tz, duration_hours, now)
        if len(await self.store.open_polls(guild_id)) >= MAX_OPEN_POLLS:
            message = content.TOO_MANY_OPEN_POLLS_MESSAGE.format(max=MAX_OPEN_POLLS)
            raise InvalidSessionInput(message)
        return await self.store.create_poll(
            guild_id=guild_id,
            channel_id=channel_id,
            name=name,
            gm_id=gm_id,
            days=days,
            times=times,
            weeks=weeks,
            duration_hours=float(duration_hours),
            min_players=min_players or 0,
            round_hours=float(round_hours or 0),
            window_start=start,
            slots=slots,
            now=now,
        )

    async def _voting_poll(self, poll_id: int) -> Poll | None:
        poll = await self.store.get_poll(poll_id)
        return poll if poll is not None and poll.status == STATUS_VOTING else None

    async def join(self, poll_id: int, user_id: int) -> JoinResult:
        async with self._lock:
            poll = await self._voting_poll(poll_id)
            if poll is None:
                return JoinResult.POLL_CLOSED
            players = await self.store.players(poll_id)
            if user_id in players:
                return JoinResult.ALREADY_JOINED
            if not await self.store.join(poll_id, user_id):
                # Under the lock nobody else can have joined this user since the check
                # above, so the store refused because the poll is full.
                return JoinResult.FULL
            await self._prefill(poll, user_id)
            return JoinResult.JOINED

    async def _prefill(self, poll: Poll, user_id: int) -> None:
        slots = await self.store.poll_slots(poll.id, poll.round_number)
        suggested = await self.prefiller.prefill(user_id, slots)
        if not suggested:
            return
        existing = await self.store.votes(poll.id, poll.round_number)
        slot_ids = {slot.id for slot in slots}
        votes = {
            slot_id: vote
            for slot_id, vote in suggested.items()
            if slot_id in slot_ids and (user_id, slot_id) not in existing
        }
        await self.store.record_votes(poll.id, user_id, votes)

    async def leave(self, poll_id: int, user_id: int) -> JoinResult:
        async with self._lock:
            poll = await self._voting_poll(poll_id)
            if poll is None:
                return JoinResult.POLL_CLOSED
            if user_id == poll.gm_id:
                return JoinResult.GM_CANNOT_LEAVE
            if user_id not in await self.store.players(poll_id):
                return JoinResult.NOT_JOINED
            await self.store.leave(poll_id, user_id)
            return JoinResult.LEFT

    async def submit_vote(
        self,
        poll_id: int,
        user_id: int,
        yes_ids: Collection[int],
        maybe_ids: Collection[int],
    ) -> VoteResult:
        """Replace the user's votes for the current round; slots in neither list are `NO`.

        A slot in both lists counts as `YES`; `((), ())` is "none of these".
        """
        async with self._lock:
            poll = await self._voting_poll(poll_id)
            if poll is None:
                return VoteResult.POLL_CLOSED
            if user_id not in await self.store.players(poll_id):
                return VoteResult.NOT_JOINED
            slot_ids = [slot.id for slot in await self.store.poll_slots(poll_id, poll.round_number)]
            if not set(yes_ids) | set(maybe_ids) <= set(slot_ids):
                return VoteResult.UNKNOWN_SLOT
            votes = {
                slot_id: Vote.YES
                if slot_id in yes_ids
                else Vote.IF_NEEDED
                if slot_id in maybe_ids
                else Vote.NO
                for slot_id in slot_ids
            }
            await self.store.record_votes(poll_id, user_id, votes)
            return VoteResult.RECORDED

    async def round_state(
        self, poll: Poll
    ) -> tuple[list[int], list[PollSlot], dict[tuple[int, int], Vote]]:
        """The players, the current round's slots and its votes."""
        players = await self.store.players(poll.id)
        slots = await self.store.poll_slots(poll.id, poll.round_number)
        votes = await self.store.votes(poll.id, poll.round_number)
        return players, slots, votes

    async def close_round(
        self, poll_id: int, by_user_id: int, is_manager: bool, reason: CloseReason
    ) -> RoundOutcome:
        """Evaluate the current round once: confirm candidates, a next round or a failure.

        Only a manual close checks permissions (GM or manager); an automatic close
        re-checks its reason here, since the round may have changed since the caller looked.
        """
        async with self._lock:
            poll = await self._voting_poll(poll_id)
            if poll is None:
                return NotVoting(await self.store.get_poll(poll_id))
            if reason == "manual" and not _may_manage(poll.gm_id, by_user_id, is_manager):
                return NotAllowed(poll)
            players, slots, votes = await self.round_state(poll)
            slot_ids = [slot.id for slot in slots]
            if reason != "manual" and (
                should_auto_close(poll, players, slot_ids, votes, self.clock()) is None
            ):
                return NotReady(poll)
            pending = tuple(pending_players(players, slot_ids, votes))
            result = _evaluate(poll, players, slot_ids, votes)
            if isinstance(result, Fits):
                await self.store.set_status(poll.id, STATUS_AWAITING_CONFIRM)
                by_id = {slot.id: slot for slot in slots}
                return RoundFits(
                    await self._reload(poll),
                    result.top,
                    tuple(by_id[score.slot_id] for score in result.top),
                    pending,
                )
            if isinstance(result, NextRound):
                return await self._advance(poll, pending)
            await self.store.set_status(poll.id, STATUS_FAILED)
            return RoundFailed(await self._reload(poll), FailReason.LAST_ROUND, pending)

    async def rebuild_outcome(self, poll_id: int) -> RoundFits | RoundAdvanced | None:
        """The outcome to post again for a poll whose outcome message was never sent.

        `awaiting_confirm`: the fitting top slots (as `confirm` evaluates them). Voting past
        round 1: the current slots and who hasn't voted in them. None for anything else.
        Changes nothing.
        """
        async with self._lock:
            poll = await self.store.get_poll(poll_id)
            if poll is None:
                return None
            if poll.status == STATUS_AWAITING_CONFIRM:
                players, slots, votes = await self.round_state(poll)
                slot_ids = [slot.id for slot in slots]
                result = _evaluate(poll, players, slot_ids, votes)
                if not isinstance(result, Fits):
                    return None
                by_id = {slot.id: slot for slot in slots}
                return RoundFits(
                    poll,
                    result.top,
                    tuple(by_id[score.slot_id] for score in result.top),
                    tuple(pending_players(players, slot_ids, votes)),
                )
            if poll.status == STATUS_VOTING and poll.round_number > 1:
                players, slots, votes = await self.round_state(poll)
                pending = pending_players(players, [slot.id for slot in slots], votes)
                return RoundAdvanced(poll, tuple(slots), tuple(pending))
            return None

    async def overview(self, guild_id: int) -> Overview:
        """`/sessions`: the guild's open polls, upcoming sessions and who each voting poll
        still waits for."""
        polls = await self.store.open_polls(guild_id)
        pending: dict[int, tuple[int, ...]] = {}
        for poll in polls:
            if poll.status == STATUS_VOTING:
                players, slots, votes = await self.round_state(poll)
                pending[poll.id] = tuple(
                    pending_players(players, [slot.id for slot in slots], votes)
                )
        sessions = await self.store.upcoming_sessions(guild_id, self.clock())
        return Overview(polls, sessions, pending)

    async def _reload(self, poll: Poll) -> Poll:
        reloaded = await self.store.get_poll(poll.id)
        assert reloaded is not None
        return reloaded

    async def _advance(self, poll: Poll, pending: tuple[int, ...]) -> RoundOutcome:
        window_start = next_window_start(poll.window_start, poll.weeks)
        try:
            new_slots = generate_slots(
                window_start,
                poll.weeks,
                poll.days,
                poll.times,
                self.tz,
                poll.duration_hours,
                self.clock(),
            )
        except InvalidSessionInput:
            await self.store.set_status(poll.id, STATUS_FAILED)
            return RoundFailed(await self._reload(poll), FailReason.NO_SLOTS, pending)
        advanced = await self.store.advance_round(poll.id, new_slots, window_start, self.clock())
        slots = await self.store.poll_slots(poll.id, advanced.round_number)
        return RoundAdvanced(replace(advanced, message_id=poll.message_id), tuple(slots), pending)

    async def confirm(
        self, poll_id: int, slot_id: int, by_user_id: int, is_manager: bool
    ) -> Session:
        """Turn an offered slot into a session; raises `SessionRefused`.

        The roster is the joined players (join order) who voted Yes or If needed on it.
        """
        async with self._lock:
            poll = await self.store.get_poll(poll_id)
            if poll is None or poll.status not in OPEN_STATUSES:
                raise SessionRefused(content.UNKNOWN_POLL_MESSAGE)
            if not _may_manage(poll.gm_id, by_user_id, is_manager):
                raise SessionRefused(content.NOT_ALLOWED_MESSAGE)
            if poll.status != STATUS_AWAITING_CONFIRM:
                raise SessionRefused(content.NOT_AWAITING_CONFIRM_MESSAGE)
            players, slots, votes = await self.round_state(poll)
            slot_ids = [slot.id for slot in slots]
            # Votes are frozen outside `voting`, so this is the round's original result.
            result = _evaluate(poll, players, slot_ids, votes)
            if not isinstance(result, Fits) or slot_id not in {s.slot_id for s in result.top}:
                raise SessionRefused(content.SLOT_NOT_OFFERED_MESSAGE)
            roster = [
                player
                for player in players
                if votes.get((player, slot_id)) in (Vote.YES, Vote.IF_NEEDED)
            ]
            return await self.store.confirm(poll.id, slot_id, roster, self.clock())

    async def cancel_poll(
        self, poll_id: int, by_user_id: int, is_manager: bool, guild_id: int | None = None
    ) -> Poll:
        """Cancel an open poll (GM or manager; in `guild_id` when given); raises
        `SessionRefused`."""
        async with self._lock:
            poll = await self.store.get_poll(poll_id)
            if (
                poll is None
                or poll.status not in OPEN_STATUSES
                or (guild_id is not None and poll.guild_id != guild_id)
            ):
                raise SessionRefused(content.UNKNOWN_POLL_MESSAGE)
            if not _may_manage(poll.gm_id, by_user_id, is_manager):
                raise SessionRefused(content.NOT_ALLOWED_MESSAGE)
            await self.store.cancel_poll(poll.id)
            return await self._reload(poll)

    async def cancel_session(
        self, session_id: int, by_user_id: int, is_manager: bool, guild_id: int | None = None
    ) -> Session:
        """Cancel a session that hasn't started (GM or manager; in `guild_id` when given);
        raises `SessionRefused`."""
        session = await self.store.get_session(session_id)
        if (
            session is None
            or session.cancelled
            or session.start <= self.clock()
            or (guild_id is not None and session.guild_id != guild_id)
        ):
            raise SessionRefused(content.UNKNOWN_SESSION_MESSAGE)
        if not _may_manage(session.gm_id, by_user_id, is_manager):
            raise SessionRefused(content.NOT_ALLOWED_MESSAGE)
        await self.store.cancel_session(session.id)
        return replace(session, cancelled=True)
