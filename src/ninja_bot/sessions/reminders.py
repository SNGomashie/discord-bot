"""Which session reminders and poll nudges are due now; no Discord, injectable clock."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Sequence

from ninja_bot.sessions.scoring import pending_players
from ninja_bot.sessions.store import (
    REMINDER_OFFSETS as REMINDER_OFFSETS,  # re-exported; lives in store.py for `confirm`
)
from ninja_bot.sessions.store import (
    STATUS_VOTING,
    Poll,
    Session,
    SessionStore,
)

NUDGE_INTERVAL_SECONDS = 86_400
MAX_NUDGES = 3


def _reached_offsets(session: Session, now: float, sent: Collection[int]) -> list[int]:
    """Unsent offsets whose time has come, smallest (closest to the start) first."""
    if now >= session.start:
        return []
    return sorted(
        offset
        for offset in REMINDER_OFFSETS
        if offset not in sent and session.start - offset <= now
    )


def due_reminder(session: Session, now: float, sent: Collection[int]) -> int | None:
    """The reminder offset to post now, or None; never once the session has started."""
    reached = _reached_offsets(session, now, sent)
    return reached[0] if reached else None


def skipped_offsets(session: Session, now: float, sent: Collection[int]) -> list[int]:
    """Earlier reminders overtaken by the due one (e.g. the bot was down): mark, don't post."""
    return _reached_offsets(session, now, sent)[1:]


def due_nudge(
    poll: Poll,
    pending: Sequence[int],
    nudge_count: int,
    last_nudge: float | None,
    now: float,
) -> bool:
    """Whether to nudge the poll's non-voters now (spaced a day apart, capped per round)."""
    if poll.status != STATUS_VOTING or not pending or nudge_count >= MAX_NUDGES:
        return False
    since = poll.round_started_at if last_nudge is None else last_nudge
    return now >= since + NUDGE_INTERVAL_SECONDS


class SessionNotifier:
    """Reads the store for due reminders and nudges; `confirm_*` after every send attempt."""

    def __init__(self, store: SessionStore, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    async def due_reminders(self) -> list[tuple[Session, int]]:
        """(session, offset) pairs to post now; overtaken earlier offsets are marked sent."""
        now = self.clock()
        due: list[tuple[Session, int]] = []
        for session in await self.store.active_sessions(now):
            if session.start <= now:
                continue
            sent = await self.store.reminders_sent(session.id)
            offset = due_reminder(session, now, sent)
            if offset is None:
                continue
            for skipped in skipped_offsets(session, now, sent):
                await self.store.mark_reminder(session.id, skipped)
            due.append((session, offset))
        return due

    async def due_nudges(self) -> list[tuple[Poll, list[int]]]:
        """(poll, pending player ids) for every voting poll that should be nudged now."""
        now = self.clock()
        due: list[tuple[Poll, list[int]]] = []
        for poll in await self.store.voting_polls():
            players = await self.store.players(poll.id)
            slots = await self.store.poll_slots(poll.id, poll.round_number)
            votes = await self.store.votes(poll.id, poll.round_number)
            pending = pending_players(players, [slot.id for slot in slots], votes)
            if not pending:
                continue
            count = await self.store.nudge_count(poll.id, poll.round_number)
            last = await self.store.last_nudge(poll.id, poll.round_number)
            if due_nudge(poll, pending, count, last, now):
                due.append((poll, pending))
        return due

    async def confirm_reminder(self, session_id: int, offset: int) -> None:
        """Mark the reminder sent; call after every send attempt, even a failed one."""
        await self.store.mark_reminder(session_id, offset)

    async def confirm_nudge(self, poll_id: int, round_number: int) -> None:
        """Count a nudge for the round; call after every send attempt, even a failed one."""
        await self.store.record_nudge(poll_id, round_number, self.clock())
