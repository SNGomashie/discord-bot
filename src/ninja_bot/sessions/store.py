"""Session poll storage: polls, their round slots and players (one SQLite file)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Protocol

from ninja_bot.sessions.scoring import MAX_PLAYERS
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

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY,"
    " guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NULL,"
    " name TEXT NOT NULL, gm_id INTEGER NOT NULL, days TEXT NOT NULL, times TEXT NOT NULL,"
    " weeks INTEGER NOT NULL, duration_hours REAL NOT NULL, min_players INTEGER NOT NULL,"
    " round_hours REAL NOT NULL, round_number INTEGER NOT NULL, window_start TEXT NOT NULL,"
    " status TEXT NOT NULL, created_at REAL NOT NULL, round_started_at REAL NOT NULL)",
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
)

_POLL_COLUMNS = (
    "id, guild_id, channel_id, message_id, name, gm_id, days, times, weeks, duration_hours,"
    " min_players, round_hours, round_number, window_start, status, created_at, round_started_at"
)
_SLOT_COLUMNS = 'id, poll_id, round_number, start, "end"'


@dataclass(frozen=True)
class Poll:
    """A session poll; `message_id` is None until the poll message is sent."""

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


@dataclass(frozen=True)
class PollSlot:
    """A candidate slot of one poll round (UTC Unix seconds)."""

    id: int
    poll_id: int
    round_number: int
    start: float
    end: float


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

    async def set_status(self, poll_id: int, status: str) -> None:
        """Set the poll's status (one of `POLL_STATUSES`; anything else raises ValueError)."""
        ...


class SqliteSessionStore:
    """Session polls, slots and players in one SQLite file; I/O in a thread."""

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

    def _set_status(self, poll_id: int, status: str) -> None:
        if status not in POLL_STATUSES:
            raise ValueError(f"unknown poll status: {status!r}")
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE polls SET status = ? WHERE id = ?", (status, poll_id))

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

    async def set_status(self, poll_id: int, status: str) -> None:
        await asyncio.to_thread(self._set_status, poll_id, status)
