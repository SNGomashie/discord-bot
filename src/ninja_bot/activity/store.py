from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS voice_sessions (id INTEGER PRIMARY KEY,"
    " user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, start_ts REAL NOT NULL,"
    " end_ts REAL NULL, last_seen REAL)",
    "CREATE INDEX IF NOT EXISTS voice_sessions_user_start ON voice_sessions (user_id, start_ts)",
    "CREATE INDEX IF NOT EXISTS voice_sessions_start ON voice_sessions (start_ts)",
    "CREATE TABLE IF NOT EXISTS reports_posted"
    " (month_key TEXT PRIMARY KEY, posted_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS pokemon_events (message_id INTEGER PRIMARY KEY,"
    " user_id INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,"
    " pokemon TEXT NULL, shiny INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS pokemon_events_ts ON pokemon_events (ts)",
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


PokemonKind = Literal["catch", "cooldown"]


@dataclass(frozen=True)
class PokemonEvent:
    """One parsed Toasty reply to a `;pokemon` command; `message_id` is the reply's id."""

    message_id: int
    user_id: int
    ts: float
    kind: PokemonKind
    pokemon: str | None = None
    shiny: bool = False


def _pokemon_event(row: tuple[Any, ...]) -> PokemonEvent:
    message_id, user_id, ts, kind, pokemon, shiny = row
    return PokemonEvent(
        message_id=int(message_id),
        user_id=int(user_id),
        ts=float(ts),
        kind="catch" if kind == "catch" else "cooldown",
        pokemon=None if pokemon is None else str(pokemon),
        shiny=bool(shiny),
    )


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

    async def voice_user_ranges(self) -> dict[int, tuple[float, float]]:
        """Per user ever seen in voice: (start of their first session, start of their last)."""
        ...

    async def record_pokemon_event(self, event: PokemonEvent) -> None:
        """Store a parsed Toasty reply; a second event with the same `message_id` is ignored."""
        ...

    async def pokemon_events_between(self, start_ts: float, end_ts: float) -> list[PokemonEvent]:
        """Events with `start_ts <= ts < end_ts`, oldest first."""
        ...

    async def first_session_start(self) -> float | None:
        """Start of the oldest recorded session, or None without any."""
        ...

    async def mark_report_posted(self, month_key: str, now: float) -> None: ...

    async def report_posted(self, month_key: str) -> bool: ...

    async def posted_months(self) -> set[str]:
        """Keys of every month whose report was marked posted."""
        ...


class SqliteActivityStore:
    """Voice sessions, `;pokemon` events and posted reports in one SQLite file; I/O in a thread."""

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

    def _voice_user_ranges(self) -> dict[int, tuple[float, float]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT user_id, MIN(start_ts), MAX(start_ts) FROM voice_sessions GROUP BY user_id"
            ).fetchall()
        return {int(user_id): (float(first), float(last)) for user_id, first, last in rows}

    def _record_pokemon_event(self, event: PokemonEvent) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO pokemon_events"
                " (message_id, user_id, ts, kind, pokemon, shiny) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.message_id,
                    event.user_id,
                    event.ts,
                    event.kind,
                    event.pokemon,
                    int(event.shiny),
                ),
            )

    def _pokemon_events_between(self, start_ts: float, end_ts: float) -> list[PokemonEvent]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT message_id, user_id, ts, kind, pokemon, shiny FROM pokemon_events"
                " WHERE ts >= ? AND ts < ? ORDER BY ts, message_id",
                (start_ts, end_ts),
            ).fetchall()
        return [_pokemon_event(row) for row in rows]

    def _first_session_start(self) -> float | None:
        with closing(self._connect()) as conn:
            (start_ts,) = conn.execute("SELECT MIN(start_ts) FROM voice_sessions").fetchone()
        return None if start_ts is None else float(start_ts)

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

    def _posted_months(self) -> set[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT month_key FROM reports_posted").fetchall()
        return {str(month_key) for (month_key,) in rows}

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

    async def voice_user_ranges(self) -> dict[int, tuple[float, float]]:
        return await asyncio.to_thread(self._voice_user_ranges)

    async def record_pokemon_event(self, event: PokemonEvent) -> None:
        await asyncio.to_thread(self._record_pokemon_event, event)

    async def pokemon_events_between(self, start_ts: float, end_ts: float) -> list[PokemonEvent]:
        return await asyncio.to_thread(self._pokemon_events_between, start_ts, end_ts)

    async def first_session_start(self) -> float | None:
        return await asyncio.to_thread(self._first_session_start)

    async def mark_report_posted(self, month_key: str, now: float) -> None:
        await asyncio.to_thread(self._mark_report_posted, month_key, now)

    async def report_posted(self, month_key: str) -> bool:
        return await asyncio.to_thread(self._report_posted, month_key)

    async def posted_months(self) -> set[str]:
        return await asyncio.to_thread(self._posted_months)
