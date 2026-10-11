from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.activity.store import ActivityStore, Session, SqliteActivityStore

USER_A = 1
USER_B = 2
CHANNEL_X = 100
CHANNEL_Y = 200


def _exists(path: Path) -> bool:
    return path.exists()


def _index_names(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {str(name) for (name,) in rows}


def _store(tmp_path: Path) -> SqliteActivityStore:
    return SqliteActivityStore(tmp_path / "activity.sqlite3")


def _spans(sessions: list[Session]) -> list[tuple[int, int, float, float | None]]:
    return [(s.user_id, s.channel_id, s.start_ts, s.end_ts) for s in sessions]


def test_sqlite_store_satisfies_protocol(tmp_path: Path) -> None:
    store: ActivityStore = _store(tmp_path)
    assert isinstance(store, SqliteActivityStore)


async def test_open_and_close_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    [open_session] = await store.open_sessions()
    assert open_session.user_id == USER_A
    assert open_session.channel_id == CHANNEL_X
    assert open_session.start_ts == 1000.0
    assert open_session.end_ts is None
    assert open_session.last_seen == 1000.0

    await store.close_session(USER_A, 1600.0)
    assert await store.open_sessions() == []
    assert _spans(await store.sessions_between(0.0, 5000.0)) == [
        (USER_A, CHANNEL_X, 1000.0, 1600.0)
    ]


async def test_close_without_open_session_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.close_session(USER_A, 1000.0)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.close_session(USER_A, 2000.0)
    await store.close_session(USER_A, 3000.0)
    assert _spans(await store.sessions_between(0.0, 5000.0)) == [
        (USER_A, CHANNEL_X, 1000.0, 2000.0)
    ]


async def test_close_session_only_touches_that_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.open_session(USER_B, CHANNEL_X, 1100.0)
    await store.close_session(USER_A, 2000.0)
    assert [s.user_id for s in await store.open_sessions()] == [USER_B]


async def test_open_session_closes_previous_open_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.open_session(USER_A, CHANNEL_Y, 1500.0)
    open_sessions = await store.open_sessions()
    assert _spans(open_sessions) == [(USER_A, CHANNEL_Y, 1500.0, None)]
    assert _spans(await store.sessions_between(0.0, 5000.0)) == [
        (USER_A, CHANNEL_X, 1000.0, 1500.0),
        (USER_A, CHANNEL_Y, 1500.0, None),
    ]


async def test_heartbeat_updates_open_sessions_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.close_session(USER_A, 1200.0)
    await store.open_session(USER_B, CHANNEL_X, 1100.0)
    await store.heartbeat(1500.0)
    [open_session] = await store.open_sessions()
    assert open_session.user_id == USER_B
    assert open_session.last_seen == 1500.0
    [closed] = [s for s in await store.sessions_between(0.0, 5000.0) if s.user_id == USER_A]
    assert closed.last_seen == 1200.0


async def test_close_stale_uses_last_seen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.open_session(USER_B, CHANNEL_Y, 1100.0)
    await store.heartbeat(1600.0)
    # The bot "crashes" here; on restart much later the sessions end at their last heartbeat.
    restarted = _store(tmp_path)
    assert await restarted.close_stale(default_end=99999.0) == 2
    assert await restarted.open_sessions() == []
    assert _spans(await restarted.sessions_between(0.0, 100000.0)) == [
        (USER_A, CHANNEL_X, 1000.0, 1600.0),
        (USER_B, CHANNEL_Y, 1100.0, 1600.0),
    ]


async def test_close_stale_without_last_seen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    path = tmp_path / "activity.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE voice_sessions SET last_seen = NULL")
    assert await store.close_stale(default_end=1300.0) == 1
    assert [s.end_ts for s in await store.sessions_between(0.0, 5000.0)] == [1300.0]

    await store.open_session(USER_B, CHANNEL_X, 2000.0)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE voice_sessions SET last_seen = NULL WHERE end_ts IS NULL")
    assert await store.close_stale() == 1
    [closed] = [s for s in await store.sessions_between(0.0, 5000.0) if s.user_id == USER_B]
    assert closed.end_ts == 2000.0


async def test_close_stale_with_nothing_open(tmp_path: Path) -> None:
    assert await _store(tmp_path).close_stale() == 0


async def test_sessions_between_overlap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Window is [1000, 2000).
    spans = {
        10: (1200.0, 1500.0),  # inside
        11: (800.0, 1100.0),  # spanning start
        12: (1900.0, 2300.0),  # spanning end
        13: (500.0, 2500.0),  # covering the whole window
        14: (100.0, 900.0),  # before
        15: (2100.0, 2400.0),  # after
        16: (900.0, 1000.0),  # ends exactly at the window start
        17: (2000.0, 2100.0),  # starts exactly at the window end
    }
    for user_id, (start, end) in spans.items():
        await store.open_session(user_id, CHANNEL_X, start)
        await store.close_session(user_id, end)
    await store.open_session(20, CHANNEL_X, 1800.0)  # open, started inside
    await store.open_session(21, CHANNEL_X, 700.0)  # open, started before
    await store.open_session(22, CHANNEL_X, 2200.0)  # open, started after

    found = await store.sessions_between(1000.0, 2000.0)
    assert [s.user_id for s in found] == [13, 21, 11, 10, 20, 12]


async def test_known_users(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert await store.known_users() == set()
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.close_session(USER_A, 1100.0)
    await store.open_session(USER_A, CHANNEL_Y, 1200.0)
    await store.open_session(USER_B, CHANNEL_X, 1300.0)
    assert await store.known_users() == {USER_A, USER_B}


async def test_first_session_start(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert await store.first_session_start() is None
    await store.open_session(USER_A, CHANNEL_X, 2000.0)
    await store.open_session(USER_B, CHANNEL_X, 1500.0)
    assert await store.first_session_start() == 1500.0


async def test_reports_posted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert await store.report_posted("2026-09") is False
    await store.mark_report_posted("2026-09", 1000.0)
    await store.mark_report_posted("2026-09", 2000.0)
    assert await store.report_posted("2026-09") is True
    assert await store.report_posted("2026-10") is False


async def test_data_survives_new_instance(tmp_path: Path) -> None:
    first = _store(tmp_path)
    await first.open_session(USER_A, CHANNEL_X, 1000.0)
    await first.mark_report_posted("2026-09", 1000.0)
    second = _store(tmp_path)
    assert _spans(await second.open_sessions()) == [(USER_A, CHANNEL_X, 1000.0, None)]
    assert await second.report_posted("2026-09") is True
    assert await second.known_users() == {USER_A}


async def test_creates_missing_parent_directory_and_index(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "activity.sqlite3"
    assert not _exists(path.parent)
    store = SqliteActivityStore(path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    assert _exists(path)
    assert "voice_sessions_user_start" in _index_names(path)


def test_constructor_does_no_io(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "activity.sqlite3"
    SqliteActivityStore(path)
    assert not _exists(path.parent)


async def test_blocking_calls_run_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recorder(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        calls.append(func.__name__)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recorder)
    store = _store(tmp_path)
    await store.open_session(USER_A, CHANNEL_X, 1000.0)
    await store.heartbeat(1100.0)
    await store.open_sessions()
    await store.sessions_between(0.0, 2000.0)
    await store.close_session(USER_A, 1200.0)
    await store.close_stale()
    await store.known_users()
    await store.mark_report_posted("2026-09", 1300.0)
    await store.report_posted("2026-09")
    assert calls == [
        "_open_session",
        "_heartbeat",
        "_open_sessions",
        "_sessions_between",
        "_close_session",
        "_close_stale",
        "_known_users",
        "_mark_report_posted",
        "_report_posted",
    ]
