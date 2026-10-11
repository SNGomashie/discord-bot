from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Collection, Sequence
from contextlib import closing
from pathlib import Path
from typing import Protocol

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS links (discord_id INTEGER PRIMARY KEY, steam64_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS posted_matches"
    " (match_id TEXT PRIMARY KEY, posted_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS seeded_players (steam64_id TEXT PRIMARY KEY)",
)

# Stays well below SQLite's bound-parameter limit (999 on old builds).
IN_CHUNK_SIZE = 500

# Posted match IDs kept for duplicate checks; older ones are pruned.
MAX_POSTED_MATCHES = 500


class LinkStore(Protocol):
    async def get(self, discord_id: int) -> str | None: ...

    async def set(self, discord_id: int, steam64_id: str) -> None: ...

    async def remove(self, discord_id: int) -> bool: ...

    # Used by /leaderboard and the match poller.
    async def all_links(self) -> dict[int, str]: ...

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        """Steam ID -> Discord ID for linked IDs; the lowest Discord ID wins on duplicates."""
        ...


class MatchLog(Protocol):
    """Which players were seeded and which matches were already announced."""

    async def is_seeded(self, steam64_id: str) -> bool: ...

    async def mark_seeded(self, steam64_id: str) -> None: ...

    async def filter_unposted(self, match_ids: Sequence[str]) -> list[str]:
        """The IDs not posted yet, in input order."""
        ...

    async def mark_posted(self, match_ids: Sequence[str]) -> None: ...


class SqliteLinkStore:
    """Links, posted matches and seeded players in one SQLite file; disk work runs in a thread."""

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

    def _get(self, discord_id: int) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT steam64_id FROM links WHERE discord_id = ?", (discord_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def _set(self, discord_id: int, steam64_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO links (discord_id, steam64_id) VALUES (?, ?)",
                (discord_id, steam64_id),
            )

    def _remove(self, discord_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM links WHERE discord_id = ?", (discord_id,))
            return cursor.rowcount > 0

    def _all_links(self) -> dict[int, str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT discord_id, steam64_id FROM links").fetchall()
        return {int(discord_id): str(steam64_id) for discord_id, steam64_id in rows}

    def _discord_ids_for(self, steam64_ids: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        with closing(self._connect()) as conn:
            for start in range(0, len(steam64_ids), IN_CHUNK_SIZE):
                chunk = steam64_ids[start : start + IN_CHUNK_SIZE]
                placeholders = ", ".join("?" * len(chunk))
                rows = conn.execute(
                    "SELECT steam64_id, MIN(discord_id) FROM links"
                    f" WHERE steam64_id IN ({placeholders}) GROUP BY steam64_id",
                    chunk,
                ).fetchall()
                result.update({str(steam64_id): int(discord_id) for steam64_id, discord_id in rows})
        return result

    def _is_seeded(self, steam64_id: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM seeded_players WHERE steam64_id = ?", (steam64_id,)
            ).fetchone()
        return row is not None

    def _mark_seeded(self, steam64_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO seeded_players (steam64_id) VALUES (?)", (steam64_id,)
            )

    def _posted_ids(self, match_ids: list[str]) -> set[str]:
        posted: set[str] = set()
        with closing(self._connect()) as conn:
            for start in range(0, len(match_ids), IN_CHUNK_SIZE):
                chunk = match_ids[start : start + IN_CHUNK_SIZE]
                placeholders = ", ".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT match_id FROM posted_matches WHERE match_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                posted.update(str(match_id) for (match_id,) in rows)
        return posted

    def _mark_posted(self, match_ids: list[str]) -> None:
        now = time.time()
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT OR IGNORE INTO posted_matches (match_id, posted_at) VALUES (?, ?)",
                [(match_id, now) for match_id in match_ids],
            )
            conn.execute(
                "DELETE FROM posted_matches WHERE rowid NOT IN"
                " (SELECT rowid FROM posted_matches ORDER BY posted_at DESC, rowid DESC LIMIT ?)",
                (MAX_POSTED_MATCHES,),
            )

    async def get(self, discord_id: int) -> str | None:
        return await asyncio.to_thread(self._get, discord_id)

    async def set(self, discord_id: int, steam64_id: str) -> None:
        await asyncio.to_thread(self._set, discord_id, steam64_id)

    async def remove(self, discord_id: int) -> bool:
        return await asyncio.to_thread(self._remove, discord_id)

    async def all_links(self) -> dict[int, str]:
        return await asyncio.to_thread(self._all_links)

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        unique = sorted(set(steam64_ids))
        if not unique:
            return {}
        return await asyncio.to_thread(self._discord_ids_for, unique)

    async def is_seeded(self, steam64_id: str) -> bool:
        return await asyncio.to_thread(self._is_seeded, steam64_id)

    async def mark_seeded(self, steam64_id: str) -> None:
        await asyncio.to_thread(self._mark_seeded, steam64_id)

    async def filter_unposted(self, match_ids: Sequence[str]) -> list[str]:
        if not match_ids:
            return []
        posted = await asyncio.to_thread(self._posted_ids, sorted(set(match_ids)))
        return [match_id for match_id in match_ids if match_id not in posted]

    async def mark_posted(self, match_ids: Sequence[str]) -> None:
        if not match_ids:
            return
        await asyncio.to_thread(self._mark_posted, list(match_ids))
