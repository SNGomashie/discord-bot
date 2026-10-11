"""Session poll storage: polls, slots, players, votes, sessions, reminders, nudges (one file)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Protocol

from ninja_bot.sessions.scoring import MAX_PLAYERS, Vote
from ninja_bot.sessions.slots import Slot

STATUS_VOTING = "voting"
STATUS_AWAITING_CONFIRM = "awaiting_confirm"
STATUS_CONFIRMED = "confirmed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
POLL_STATUSES = frozenset(
    {STATUS_VOTING, STATUS_AWAITING_CONFIRM, STATUS_CONFIRMED, STATUS_FAILED, STATUS_CANCELLED}
)
# Polls that still need votes or a GM decision.
OPEN_STATUSES = (STATUS_VOTING, STATUS_AWAITING_CONFIRM)
# Seconds before the session start: 24 hours and 1 hour (re-exported by reminders.py).
REMINDER_OFFSETS = (86_400, 3_600)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY,"
    " guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NULL,"
    " name TEXT NOT NULL, gm_id INTEGER NOT NULL, days TEXT NOT NULL, times TEXT NOT NULL,"
    " weeks INTEGER NOT NULL, duration_hours REAL NOT NULL, min_players INTEGER NOT NULL,"
    " round_hours REAL NOT NULL, round_number INTEGER NOT NULL, window_start TEXT NOT NULL,"
    " status TEXT NOT NULL, created_at REAL NOT NULL, round_started_at REAL NOT NULL,"
    " result_message_id INTEGER NULL)",
    "CREATE INDEX IF NOT EXISTS polls_guild_status ON polls (guild_id, status)",
    "CREATE TABLE IF NOT EXISTS poll_slots (id INTEGER PRIMARY KEY,"
    " poll_id INTEGER NOT NULL, round_number INTEGER NOT NULL,"
    ' start REAL NOT NULL, "end" REAL NOT NULL)',
    "CREATE INDEX IF NOT EXISTS poll_slots_round ON poll_slots (poll_id, round_number, start)",
    "CREATE TABLE IF NOT EXISTS poll_players (poll_id INTEGER NOT NULL,"
    " user_id INTEGER NOT NULL, PRIMARY KEY (poll_id, user_id))",
    "CREATE TABLE IF NOT EXISTS poll_votes (poll_id INTEGER NOT NULL,"
    " user_id INTEGER NOT NULL, slot_id INTEGER NOT NULL, vote TEXT NOT NULL,"
    " PRIMARY KEY (poll_id, user_id, slot_id))",
    "CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY,"
    " poll_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,"
    " message_id INTEGER NULL, name TEXT NOT NULL, gm_id INTEGER NOT NULL,"
    ' start REAL NOT NULL, "end" REAL NOT NULL, roster TEXT NOT NULL,'
    " cancelled INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS sessions_guild_start ON sessions (guild_id, start)",
    "CREATE TABLE IF NOT EXISTS session_reminders (session_id INTEGER NOT NULL,"
    " offset_seconds INTEGER NOT NULL, PRIMARY KEY (session_id, offset_seconds))",
    "CREATE TABLE IF NOT EXISTS poll_nudges (poll_id INTEGER NOT NULL,"
    " round_number INTEGER NOT NULL, sent_at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS poll_nudges_round ON poll_nudges (poll_id, round_number)",
)

_POLL_COLUMNS = (
    "id, guild_id, channel_id, message_id, name, gm_id, days, times, weeks, duration_hours,"
    " min_players, round_hours, round_number, window_start, status, created_at, round_started_at,"
    " result_message_id"
)
_SLOT_COLUMNS = 'id, poll_id, round_number, start, "end"'
_SESSION_COLUMNS = (
    'id, poll_id, guild_id, channel_id, message_id, name, gm_id, start, "end", roster, cancelled'
)


@dataclass(frozen=True)
class Poll:
    """A session poll.

    `message_id` is the current round's poll message (None until it is sent);
    `result_message_id` is the result message with the Confirm buttons (None until sent).
    """

    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    name: str
    gm_id: int
    days: tuple[int, ...]
    times: tuple[time, ...]
    weeks: int
    duration_hours: float
    min_players: int
    round_hours: float
    round_number: int
    window_start: date
    status: str
    created_at: float
    round_started_at: float
    result_message_id: int | None = None


@dataclass(frozen=True)
class PollSlot:
    """A candidate slot of one poll round (UTC Unix seconds)."""

    id: int
    poll_id: int
    round_number: int
    start: float
    end: float


@dataclass(frozen=True)
class Session:
    """A confirmed session (UTC Unix seconds); `message_id` is the poll's message, if any."""

    id: int
    poll_id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    name: str
    gm_id: int
    start: float
    end: float
    roster: tuple[int, ...]
    cancelled: bool


def _join_days(days: Sequence[int]) -> str:
    return ",".join(str(day) for day in days)


def _join_times(times: Sequence[time]) -> str:
    return ",".join(at.strftime("%H:%M") for at in times)


def _split_days(text: str) -> tuple[int, ...]:
    return tuple(int(day) for day in text.split(",")) if text else ()


def _split_times(text: str) -> tuple[time, ...]:
    return tuple(time.fromisoformat(at) for at in text.split(",")) if text else ()


def _poll(row: tuple[Any, ...]) -> Poll:
    (
        poll_id,
        guild_id,
        channel_id,
        message_id,
        name,
        gm_id,
        days,
        times,
        weeks,
        duration_hours,
        min_players,
        round_hours,
        round_number,
        window_start,
        status,
        created_at,
        round_started_at,
        result_message_id,
    ) = row
    return Poll(
        id=int(poll_id),
        guild_id=int(guild_id),
        channel_id=int(channel_id),
        message_id=None if message_id is None else int(message_id),
        name=str(name),
        gm_id=int(gm_id),
        days=_split_days(str(days)),
        times=_split_times(str(times)),
        weeks=int(weeks),
        duration_hours=float(duration_hours),
        min_players=int(min_players),
        round_hours=float(round_hours),
        round_number=int(round_number),
        window_start=date.fromisoformat(str(window_start)),
        status=str(status),
        created_at=float(created_at),
        round_started_at=float(round_started_at),
        result_message_id=None if result_message_id is None else int(result_message_id),
    )


def _join_ids(ids: Sequence[int]) -> str:
    return ",".join(str(user_id) for user_id in ids)


def _split_ids(text: str) -> tuple[int, ...]:
    return tuple(int(user_id) for user_id in text.split(",")) if text else ()


def _session(row: tuple[Any, ...]) -> Session:
    (
        session_id,
        poll_id,
        guild_id,
        channel_id,
        message_id,
        name,
        gm_id,
        start,
        end,
        roster,
        cancelled,
    ) = row
    return Session(
        id=int(session_id),
        poll_id=int(poll_id),
        guild_id=int(guild_id),
        channel_id=int(channel_id),
        message_id=None if message_id is None else int(message_id),
        name=str(name),
        gm_id=int(gm_id),
        start=float(start),
        end=float(end),
        roster=_split_ids(str(roster)),
        cancelled=bool(cancelled),
    )


def _poll_slot(row: tuple[Any, ...]) -> PollSlot:
    slot_id, poll_id, round_number, start, end = row
    return PollSlot(
        id=int(slot_id),
        poll_id=int(poll_id),
        round_number=int(round_number),
        start=float(start),
        end=float(end),
    )


class SessionStore(Protocol):
    async def create_poll(
        self,
        *,
        guild_id: int,
        channel_id: int,
        name: str,
        gm_id: int,
        days: Sequence[int],
        times: Sequence[time],
        weeks: int,
        duration_hours: float,
        min_players: int,
        round_hours: float,
        window_start: date,
        slots: Sequence[Slot],
        now: float,
    ) -> Poll:
        """Insert a `voting` poll in round 1 with its slots and the GM joined, atomically."""
        ...

    async def get_poll(self, poll_id: int) -> Poll | None:
        """The poll, or None if there is no poll with that id."""
        ...

    async def set_poll_message(self, poll_id: int, message_id: int) -> None:
        """Remember the Discord message that shows the poll."""
        ...

    async def set_result_message(self, poll_id: int, message_id: int) -> None:
        """Remember the result message (the one with the Confirm buttons)."""
        ...

    async def polls_missing_messages(self) -> list[Poll]:
        """Polls whose outcome message was never posted, oldest first.

        Voting polls past round 1 without `message_id` (the next-round message failed) and
        `awaiting_confirm` polls without `result_message_id` (the result failed). Round 1 is
        excluded: a failed first post is discarded when the poll is created.
        """
        ...

    async def poll_slots(self, poll_id: int, round_number: int) -> list[PollSlot]:
        """The slots of one round, oldest start first."""
        ...

    async def join(self, poll_id: int, user_id: int) -> bool:
        """Add a player; False when already joined or the poll has `MAX_PLAYERS` players."""
        ...

    async def leave(self, poll_id: int, user_id: int) -> None:
        """Remove a player and all of their votes in the poll; no-op if not joined."""
        ...

    async def players(self, poll_id: int) -> list[int]:
        """User ids of the joined players, in join order."""
        ...

    async def open_polls(self, guild_id: int) -> list[Poll]:
        """The guild's `voting` and `awaiting_confirm` polls, oldest first."""
        ...

    async def voting_polls(self) -> list[Poll]:
        """Polls in `voting` in every guild, oldest first."""
        ...

    async def set_status(self, poll_id: int, status: str) -> None:
        """Set the poll's status (one of `POLL_STATUSES`; anything else raises ValueError).

        Entering `awaiting_confirm` clears `result_message_id` (no result posted yet).
        """
        ...

    async def record_votes(self, poll_id: int, user_id: int, votes: Mapping[int, Vote]) -> None:
        """Replace the user's votes on the given slot ids, atomically."""
        ...

    async def votes(self, poll_id: int, round_number: int) -> dict[tuple[int, int], Vote]:
        """(user_id, slot_id) -> vote, for the slots of one round only."""
        ...

    async def advance_round(
        self,
        poll_id: int,
        slots: Sequence[Slot],
        window_start: date,
        now: float,
        message_id: int | None = None,
    ) -> Poll:
        """Start the next round: new slots, `voting`, `round_started_at = now`, zero nudges.

        `message_id` always replaces the poll message (None = the new round's message is not
        posted yet, so a voting poll past round 1 without one was never posted).
        """
        ...

    async def confirm(
        self, poll_id: int, slot_id: int, roster: Sequence[int], now: float
    ) -> Session:
        """Mark the poll `confirmed` and insert its session at the slot's time, atomically.

        Reminder offsets whose time has passed at `now` are marked handled in the same
        transaction, so a late confirmation never posts them (the announcement covers them).
        """
        ...

    async def cancel_poll(self, poll_id: int) -> None:
        """Set the poll's status to `cancelled`."""
        ...

    async def get_session(self, session_id: int) -> Session | None:
        """The session (cancelled or not), or None."""
        ...

    async def upcoming_sessions(self, guild_id: int, now: float) -> list[Session]:
        """The guild's uncancelled sessions starting after `now`, soonest first."""
        ...

    async def active_sessions(self, now: float) -> list[Session]:
        """Uncancelled sessions (any guild) whose end is after `now`, soonest first."""
        ...

    async def cancel_session(self, session_id: int) -> None:
        """Mark the session cancelled (it stays stored)."""
        ...

    async def mark_reminder(self, session_id: int, offset_seconds: int) -> None:
        """Remember that the reminder at that offset was handled; idempotent."""
        ...

    async def reminders_sent(self, session_id: int) -> set[int]:
        """Offsets (seconds before start) already handled for the session."""
        ...

    async def nudge_count(self, poll_id: int, round_number: int) -> int:
        """How many nudges were sent in that round."""
        ...

    async def record_nudge(self, poll_id: int, round_number: int, at: float) -> None:
        """Remember a nudge sent at `at` in that round."""
        ...

    async def last_nudge(self, poll_id: int, round_number: int) -> float | None:
        """When the round's latest nudge was sent, or None."""
        ...


class SqliteSessionStore:
    """Session polls and sessions in one SQLite file; I/O in a thread."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        if not self._ready:
            with conn:
                for statement in _SCHEMA:
                    conn.execute(statement)
            self._ready = True
        return conn

    def _create_poll(
        self,
        guild_id: int,
        channel_id: int,
        name: str,
        gm_id: int,
        days: Sequence[int],
        times: Sequence[time],
        weeks: int,
        duration_hours: float,
        min_players: int,
        round_hours: float,
        window_start: date,
        slots: Sequence[Slot],
        now: float,
    ) -> Poll:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO polls (guild_id, channel_id, message_id, name, gm_id, days, times,"
                " weeks, duration_hours, min_players, round_hours, round_number, window_start,"
                " status, created_at, round_started_at)"
                " VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    guild_id,
                    channel_id,
                    name,
                    gm_id,
                    _join_days(days),
                    _join_times(times),
                    weeks,
                    duration_hours,
                    min_players,
                    round_hours,
                    window_start.isoformat(),
                    STATUS_VOTING,
                    now,
                    now,
                ),
            )
            poll_id = cursor.lastrowid
            assert poll_id is not None
            conn.executemany(
                'INSERT INTO poll_slots (poll_id, round_number, start, "end") VALUES (?, 1, ?, ?)',
                [(poll_id, slot.start, slot.end) for slot in slots],
            )
            conn.execute(
                "INSERT INTO poll_players (poll_id, user_id) VALUES (?, ?)", (poll_id, gm_id)
            )
            row = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls WHERE id = ?", (poll_id,)
            ).fetchone()
        return _poll(row)

    def _get_poll(self, poll_id: int) -> Poll | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls WHERE id = ?", (poll_id,)
            ).fetchone()
        return None if row is None else _poll(row)

    def _set_poll_message(self, poll_id: int, message_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id))

    def _set_result_message(self, poll_id: int, message_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE polls SET result_message_id = ? WHERE id = ?", (message_id, poll_id)
            )

    def _polls_missing_messages(self) -> list[Poll]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls"
                " WHERE (status = ? AND round_number > 1 AND message_id IS NULL)"
                " OR (status = ? AND result_message_id IS NULL)"
                " ORDER BY created_at, id",
                (STATUS_VOTING, STATUS_AWAITING_CONFIRM),
            ).fetchall()
        return [_poll(row) for row in rows]

    def _poll_slots(self, poll_id: int, round_number: int) -> list[PollSlot]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_SLOT_COLUMNS} FROM poll_slots WHERE poll_id = ? AND round_number = ?"
                " ORDER BY start, id",
                (poll_id, round_number),
            ).fetchall()
        return [_poll_slot(row) for row in rows]

    def _join(self, poll_id: int, user_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM poll_players WHERE poll_id = ?", (poll_id,)
            ).fetchone()
            if count >= MAX_PLAYERS:
                return False
            cursor = conn.execute(
                "INSERT OR IGNORE INTO poll_players (poll_id, user_id) VALUES (?, ?)",
                (poll_id, user_id),
            )
            return cursor.rowcount == 1

    def _leave(self, poll_id: int, user_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM poll_players WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
            )
            conn.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
            )

    def _players(self, poll_id: int) -> list[int]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT user_id FROM poll_players WHERE poll_id = ? ORDER BY rowid", (poll_id,)
            ).fetchall()
        return [int(user_id) for (user_id,) in rows]

    def _open_polls(self, guild_id: int) -> list[Poll]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls WHERE guild_id = ? AND status IN (?, ?)"
                " ORDER BY created_at, id",
                (guild_id, *OPEN_STATUSES),
            ).fetchall()
        return [_poll(row) for row in rows]

    def _voting_polls(self) -> list[Poll]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls WHERE status = ? ORDER BY created_at, id",
                (STATUS_VOTING,),
            ).fetchall()
        return [_poll(row) for row in rows]

    def _set_status(self, poll_id: int, status: str) -> None:
        if status not in POLL_STATUSES:
            raise ValueError(f"unknown poll status: {status!r}")
        with closing(self._connect()) as conn, conn:
            if status == STATUS_AWAITING_CONFIRM:
                conn.execute(
                    "UPDATE polls SET status = ?, result_message_id = NULL WHERE id = ?",
                    (status, poll_id),
                )
            else:
                conn.execute("UPDATE polls SET status = ? WHERE id = ?", (status, poll_id))

    def _record_votes(self, poll_id: int, user_id: int, votes: Mapping[int, Vote]) -> None:
        if not votes:
            return
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT OR REPLACE INTO poll_votes (poll_id, user_id, slot_id, vote)"
                " VALUES (?, ?, ?, ?)",
                [(poll_id, user_id, slot_id, str(vote)) for slot_id, vote in votes.items()],
            )

    def _votes(self, poll_id: int, round_number: int) -> dict[tuple[int, int], Vote]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT v.user_id, v.slot_id, v.vote FROM poll_votes v"
                " JOIN poll_slots s ON s.id = v.slot_id AND s.poll_id = v.poll_id"
                " WHERE v.poll_id = ? AND s.round_number = ?",
                (poll_id, round_number),
            ).fetchall()
        return {(int(user_id), int(slot_id)): Vote(vote) for user_id, slot_id, vote in rows}

    def _advance_round(
        self,
        poll_id: int,
        slots: Sequence[Slot],
        window_start: date,
        now: float,
        message_id: int | None,
    ) -> Poll:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE polls SET round_number = round_number + 1, round_started_at = ?,"
                " window_start = ?, status = ?, message_id = ?"
                " WHERE id = ?",
                (now, window_start.isoformat(), STATUS_VOTING, message_id, poll_id),
            )
            row = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls WHERE id = ?", (poll_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown poll: {poll_id}")
            poll = _poll(row)
            conn.executemany(
                'INSERT INTO poll_slots (poll_id, round_number, start, "end") VALUES (?, ?, ?, ?)',
                [(poll_id, poll.round_number, slot.start, slot.end) for slot in slots],
            )
            # Nudges are counted per round; drop any stray rows for the new round.
            conn.execute(
                "DELETE FROM poll_nudges WHERE poll_id = ? AND round_number = ?",
                (poll_id, poll.round_number),
            )
        return poll

    def _confirm(self, poll_id: int, slot_id: int, roster: Sequence[int], now: float) -> Session:
        with closing(self._connect()) as conn, conn:
            poll_row = conn.execute(
                f"SELECT {_POLL_COLUMNS} FROM polls WHERE id = ?", (poll_id,)
            ).fetchone()
            slot_row = conn.execute(
                f"SELECT {_SLOT_COLUMNS} FROM poll_slots WHERE id = ? AND poll_id = ?",
                (slot_id, poll_id),
            ).fetchone()
            if poll_row is None or slot_row is None:
                raise ValueError(f"unknown poll {poll_id} or slot {slot_id}")
            poll, slot = _poll(poll_row), _poll_slot(slot_row)
            conn.execute("UPDATE polls SET status = ? WHERE id = ?", (STATUS_CONFIRMED, poll_id))
            cursor = conn.execute(
                "INSERT INTO sessions (poll_id, guild_id, channel_id, message_id, name, gm_id,"
                ' start, "end", roster, cancelled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)',
                (
                    poll.id,
                    poll.guild_id,
                    poll.channel_id,
                    poll.message_id,
                    poll.name,
                    poll.gm_id,
                    slot.start,
                    slot.end,
                    _join_ids(roster),
                ),
            )
            session_id = cursor.lastrowid
            assert session_id is not None
            conn.executemany(
                "INSERT OR IGNORE INTO session_reminders (session_id, offset_seconds)"
                " VALUES (?, ?)",
                [(session_id, offset) for offset in REMINDER_OFFSETS if slot.start - offset <= now],
            )
        return Session(
            id=session_id,
            poll_id=poll.id,
            guild_id=poll.guild_id,
            channel_id=poll.channel_id,
            message_id=poll.message_id,
            name=poll.name,
            gm_id=poll.gm_id,
            start=slot.start,
            end=slot.end,
            roster=tuple(roster),
            cancelled=False,
        )

    def _get_session(self, session_id: int) -> Session | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return None if row is None else _session(row)

    def _upcoming_sessions(self, guild_id: int, now: float) -> list[Session]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions"
                " WHERE guild_id = ? AND cancelled = 0 AND start > ? ORDER BY start, id",
                (guild_id, now),
            ).fetchall()
        return [_session(row) for row in rows]

    def _active_sessions(self, now: float) -> list[Session]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions"
                ' WHERE cancelled = 0 AND "end" > ? ORDER BY start, id',
                (now,),
            ).fetchall()
        return [_session(row) for row in rows]

    def _cancel_session(self, session_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE sessions SET cancelled = 1 WHERE id = ?", (session_id,))

    def _mark_reminder(self, session_id: int, offset_seconds: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO session_reminders (session_id, offset_seconds)"
                " VALUES (?, ?)",
                (session_id, offset_seconds),
            )

    def _reminders_sent(self, session_id: int) -> set[int]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT offset_seconds FROM session_reminders WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        return {int(offset) for (offset,) in rows}

    def _nudge_stats(self, poll_id: int, round_number: int) -> tuple[int, float | None]:
        with closing(self._connect()) as conn:
            count, last = conn.execute(
                "SELECT COUNT(*), MAX(sent_at) FROM poll_nudges"
                " WHERE poll_id = ? AND round_number = ?",
                (poll_id, round_number),
            ).fetchone()
        return int(count), None if last is None else float(last)

    def _record_nudge(self, poll_id: int, round_number: int, at: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO poll_nudges (poll_id, round_number, sent_at) VALUES (?, ?, ?)",
                (poll_id, round_number, at),
            )

    async def create_poll(
        self,
        *,
        guild_id: int,
        channel_id: int,
        name: str,
        gm_id: int,
        days: Sequence[int],
        times: Sequence[time],
        weeks: int,
        duration_hours: float,
        min_players: int,
        round_hours: float,
        window_start: date,
        slots: Sequence[Slot],
        now: float,
    ) -> Poll:
        return await asyncio.to_thread(
            self._create_poll,
            guild_id,
            channel_id,
            name,
            gm_id,
            days,
            times,
            weeks,
            duration_hours,
            min_players,
            round_hours,
            window_start,
            slots,
            now,
        )

    async def get_poll(self, poll_id: int) -> Poll | None:
        return await asyncio.to_thread(self._get_poll, poll_id)

    async def set_poll_message(self, poll_id: int, message_id: int) -> None:
        await asyncio.to_thread(self._set_poll_message, poll_id, message_id)

    async def set_result_message(self, poll_id: int, message_id: int) -> None:
        await asyncio.to_thread(self._set_result_message, poll_id, message_id)

    async def polls_missing_messages(self) -> list[Poll]:
        return await asyncio.to_thread(self._polls_missing_messages)

    async def poll_slots(self, poll_id: int, round_number: int) -> list[PollSlot]:
        return await asyncio.to_thread(self._poll_slots, poll_id, round_number)

    async def join(self, poll_id: int, user_id: int) -> bool:
        return await asyncio.to_thread(self._join, poll_id, user_id)

    async def leave(self, poll_id: int, user_id: int) -> None:
        await asyncio.to_thread(self._leave, poll_id, user_id)

    async def players(self, poll_id: int) -> list[int]:
        return await asyncio.to_thread(self._players, poll_id)

    async def open_polls(self, guild_id: int) -> list[Poll]:
        return await asyncio.to_thread(self._open_polls, guild_id)

    async def voting_polls(self) -> list[Poll]:
        return await asyncio.to_thread(self._voting_polls)

    async def set_status(self, poll_id: int, status: str) -> None:
        await asyncio.to_thread(self._set_status, poll_id, status)

    async def record_votes(self, poll_id: int, user_id: int, votes: Mapping[int, Vote]) -> None:
        await asyncio.to_thread(self._record_votes, poll_id, user_id, dict(votes))

    async def votes(self, poll_id: int, round_number: int) -> dict[tuple[int, int], Vote]:
        return await asyncio.to_thread(self._votes, poll_id, round_number)

    async def advance_round(
        self,
        poll_id: int,
        slots: Sequence[Slot],
        window_start: date,
        now: float,
        message_id: int | None = None,
    ) -> Poll:
        return await asyncio.to_thread(
            self._advance_round, poll_id, tuple(slots), window_start, now, message_id
        )

    async def confirm(
        self, poll_id: int, slot_id: int, roster: Sequence[int], now: float
    ) -> Session:
        return await asyncio.to_thread(self._confirm, poll_id, slot_id, tuple(roster), now)

    async def cancel_poll(self, poll_id: int) -> None:
        await asyncio.to_thread(self._set_status, poll_id, STATUS_CANCELLED)

    async def get_session(self, session_id: int) -> Session | None:
        return await asyncio.to_thread(self._get_session, session_id)

    async def upcoming_sessions(self, guild_id: int, now: float) -> list[Session]:
        return await asyncio.to_thread(self._upcoming_sessions, guild_id, now)

    async def active_sessions(self, now: float) -> list[Session]:
        return await asyncio.to_thread(self._active_sessions, now)

    async def cancel_session(self, session_id: int) -> None:
        await asyncio.to_thread(self._cancel_session, session_id)

    async def mark_reminder(self, session_id: int, offset_seconds: int) -> None:
        await asyncio.to_thread(self._mark_reminder, session_id, offset_seconds)

    async def reminders_sent(self, session_id: int) -> set[int]:
        return await asyncio.to_thread(self._reminders_sent, session_id)

    async def nudge_count(self, poll_id: int, round_number: int) -> int:
        count, _ = await asyncio.to_thread(self._nudge_stats, poll_id, round_number)
        return count

    async def record_nudge(self, poll_id: int, round_number: int, at: float) -> None:
        await asyncio.to_thread(self._record_nudge, poll_id, round_number, at)

    async def last_nudge(self, poll_id: int, round_number: int) -> float | None:
        _, last = await asyncio.to_thread(self._nudge_stats, poll_id, round_number)
        return last
