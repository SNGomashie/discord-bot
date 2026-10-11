"""Session poll rules: create, join/leave and vote (no discord import)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo

from ninja_bot.sessions import content
from ninja_bot.sessions.scoring import MAX_PLAYERS, Vote
from ninja_bot.sessions.slots import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_WEEKS,
    MAX_WEEKS,
    InvalidSessionInput,
    generate_slots,
    parse_days,
    parse_start_date,
    parse_times,
)
from ninja_bot.sessions.store import STATUS_VOTING, Poll, PollSlot, SessionStore

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
        poll = await self._voting_poll(poll_id)
        if poll is None:
            return JoinResult.POLL_CLOSED
        players = await self.store.players(poll_id)
        if user_id in players:
            return JoinResult.ALREADY_JOINED
        if not await self.store.join(poll_id, user_id):
            # Not joined before, so the store refused because the poll is full.
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
