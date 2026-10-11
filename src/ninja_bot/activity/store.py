from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS voice_sessions (id INTEGER PRIMARY KEY,"
    " user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, start_ts REAL NOT NULL,"
    " end_ts REAL NULL, last_seen REAL)",
    "CREATE INDEX IF NOT EXISTS voice_sessions_user_start ON voice_sessions (user_id, start_ts)",
    "CREATE TABLE IF NOT EXISTS reports_posted"
    " (month_key TEXT PRIMARY KEY, posted_at REAL NOT NULL)",
)

_SESSION_COLUMNS = "id, user_id, channel_id, start_ts, end_ts, last_seen"


@dataclass(frozen=True)
class Session:
    """One stretch of voice time; `end_ts` is None while the session is open."""

    id: int
    user_id: int
    channel_id: int
    start_ts: float
    end_ts: float | None
    last_seen: float | None


def _session(row: tuple[Any, ...]) -> Session:
    session_id, user_id, channel_id, start_ts, end_ts, last_seen = row
    return Session(
        id=int(session_id),
        user_id=int(user_id),
        channel_id=int(channel_id),
        start_ts=float(start_ts),
        end_ts=None if end_ts is None else float(end_ts),
        last_seen=None if last_seen is None else float(last_seen),
    )


class ActivityStore(Protocol):
    async def open_session(self, user_id: int, channel_id: int, now: float) -> None:
        """Start a session; any open session of that user is closed at `now` first."""
        ...

    async def close_session(self, user_id: int, now: float) -> None:
        """Close the user's open session at `now`; no-op if there is none."""
        ...

    async def heartbeat(self, now: float) -> None:
        """Set `last_seen` on every open session."""
        ...

    async def close_stale(self, default_end: float | None = None) -> int:
        """Close every open session at its `last_seen` (or `default_end` without one).

        Sessions with neither are closed at their start. Returns how many were closed.
        """
        ...

    async def open_sessions(self) -> list[Session]: ...

    async def sessions_between(self, start_ts: float, end_ts: float) -> list[Session]:
        """Sessions overlapping [start_ts, end_ts), open ones included, oldest first."""
        ...

    async def known_users(self) -> set[int]:
        """Every user ever seen."""
        ...

    async def mark_report_posted(self, month_key: str, now: float) -> None: ...

    async def report_posted(self, month_key: str) -> bool: ...


class SqliteActivityStore:
    """Voice sessions and posted monthly reports in one SQLite file; disk work runs in a thread."""

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

    def _open_session(self, user_id: int, channel_id: int, now: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE voice_sessions SET end_ts = ?, last_seen = ?"
                " WHERE user_id = ? AND end_ts IS NULL",
                (now, now, user_id),
            )
            conn.execute(
                "INSERT INTO voice_sessions (user_id, channel_id, start_ts, end_ts, last_seen)"
                " VALUES (?, ?, ?, NULL, ?)",
                (user_id, channel_id, now, now),
            )

    def _close_session(self, user_id: int, now: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE voice_sessions SET end_ts = ?, last_seen = ?"
                " WHERE user_id = ? AND end_ts IS NULL",
                (now, now, user_id),
            )

    def _heartbeat(self, now: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE voice_sessions SET last_seen = ? WHERE end_ts IS NULL", (now,))

    def _close_stale(self, default_end: float | None) -> int:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE voice_sessions SET end_ts = MAX(start_ts, COALESCE(last_seen, ?, start_ts))"
                " WHERE end_ts IS NULL",
                (default_end,),
            )
            return cursor.rowcount

    def _open_sessions(self) -> list[Session]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM voice_sessions WHERE end_ts IS NULL"
                " ORDER BY start_ts, id"
            ).fetchall()
        return [_session(row) for row in rows]

    def _sessions_between(self, start_ts: float, end_ts: float) -> list[Session]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM voice_sessions"
                " WHERE start_ts < ? AND (end_ts IS NULL OR end_ts > ?)"
                " ORDER BY start_ts, id",
                (end_ts, start_ts),
            ).fetchall()
        return [_session(row) for row in rows]

    def _known_users(self) -> set[int]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT DISTINCT user_id FROM voice_sessions").fetchall()
        return {int(user_id) for (user_id,) in rows}

    def _mark_report_posted(self, month_key: str, now: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO reports_posted (month_key, posted_at) VALUES (?, ?)",
                (month_key, now),
            )

    def _report_posted(self, month_key: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM reports_posted WHERE month_key = ?", (month_key,)
            ).fetchone()
        return row is not None

    async def open_session(self, user_id: int, channel_id: int, now: float) -> None:
        await asyncio.to_thread(self._open_session, user_id, channel_id, now)

    async def close_session(self, user_id: int, now: float) -> None:
        await asyncio.to_thread(self._close_session, user_id, now)

    async def heartbeat(self, now: float) -> None:
        await asyncio.to_thread(self._heartbeat, now)

    async def close_stale(self, default_end: float | None = None) -> int:
        return await asyncio.to_thread(self._close_stale, default_end)

    async def open_sessions(self) -> list[Session]:
        return await asyncio.to_thread(self._open_sessions)

    async def sessions_between(self, start_ts: float, end_ts: float) -> list[Session]:
        return await asyncio.to_thread(self._sessions_between, start_ts, end_ts)

    async def known_users(self) -> set[int]:
        return await asyncio.to_thread(self._known_users)

    async def mark_report_posted(self, month_key: str, now: float) -> None:
        await asyncio.to_thread(self._mark_report_posted, month_key, now)

    async def report_posted(self, month_key: str) -> bool:
        return await asyncio.to_thread(self._report_posted, month_key)
