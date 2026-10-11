from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Collection
from contextlib import closing
from pathlib import Path
from typing import Protocol

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS links (discord_id INTEGER PRIMARY KEY, steam64_id TEXT NOT NULL)"
)

# Stays well below SQLite's bound-parameter limit (999 on old builds).
IN_CHUNK_SIZE = 500


class LinkStore(Protocol):
    async def get(self, discord_id: int) -> str | None: ...

    async def set(self, discord_id: int, steam64_id: str) -> None: ...

    async def remove(self, discord_id: int) -> bool: ...

    # Used by the upcoming leaderboard and match poller.
    async def all_links(self) -> dict[int, str]: ...

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        """Steam ID -> Discord ID for linked IDs; the lowest Discord ID wins on duplicates."""
        ...


class SqliteLinkStore:
    """Discord ID -> Steam64 ID links in a SQLite file; all disk work runs in a thread."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        if not self._ready:
            with conn:
                conn.execute(_SCHEMA)
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

    async def get(self, discord_id: int) -> str | None:
        return await asyncio.to_thread(self._get, discord_id)

    async def set(self, discord_id: int, steam64_id: str) -> None:
        await asyncio.to_thread(self._set, discord_id, steam64_id)

    async def remove(self, discord_id: int) -> bool:
        return await asyncio.to_thread(self._remove, discord_id)

    # Used by the upcoming leaderboard and match poller.
    async def all_links(self) -> dict[int, str]:
        return await asyncio.to_thread(self._all_links)

    async def discord_ids_for(self, steam64_ids: Collection[str]) -> dict[str, int]:
        unique = sorted(set(steam64_ids))
        if not unique:
            return {}
        return await asyncio.to_thread(self._discord_ids_for, unique)
