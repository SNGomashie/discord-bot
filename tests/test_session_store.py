from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, time
from pathlib import Path

import pytest

from ninja_bot.sessions.scoring import MAX_PLAYERS
from ninja_bot.sessions.slots import Slot
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_VOTING,
    Poll,
    SessionStore,
    SqliteSessionStore,
)

GUILD = 10
OTHER_GUILD = 11
CHANNEL = 100
GM = 1
PLAYER = 2
HOUR = 3600.0


def _store(tmp_path: Path) -> SqliteSessionStore:
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


async def _create(
    store: SqliteSessionStore,
    *,
    guild_id: int = GUILD,
    now: float = 1000.0,
    slots: tuple[Slot, ...] = (Slot(9000.0, 9000.0 + 4 * HOUR), Slot(5000.0, 5000.0 + 4 * HOUR)),
) -> Poll:
    return await store.create_poll(
        guild_id=guild_id,
        channel_id=CHANNEL,
        name="Curse of Strahd",
        gm_id=GM,
        days=(4, 5),
        times=(time(14, 0), time(19, 30)),
        weeks=2,
        duration_hours=4.0,
        min_players=3,
        round_hours=48.0,
        window_start=date(2026, 11, 6),
        slots=slots,
        now=now,
    )


def test_sqlite_store_satisfies_protocol(tmp_path: Path) -> None:
    store: SessionStore = _store(tmp_path)
    assert isinstance(store, SqliteSessionStore)


async def test_create_and_get_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = await _create(store)

    assert created == Poll(
        id=created.id,
        guild_id=GUILD,
        channel_id=CHANNEL,
        message_id=None,
        name="Curse of Strahd",
        gm_id=GM,
        days=(4, 5),
        times=(time(14, 0), time(19, 30)),
        weeks=2,
        duration_hours=4.0,
        min_players=3,
        round_hours=48.0,
        round_number=1,
        window_start=date(2026, 11, 6),
        status=STATUS_VOTING,
        created_at=1000.0,
        round_started_at=1000.0,
    )
    assert await store.get_poll(created.id) == created
    assert await store.get_poll(created.id + 1) is None


async def test_set_poll_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.set_poll_message(poll.id, 555)
    fetched = await store.get_poll(poll.id)
    assert fetched is not None
    assert fetched.message_id == 555


async def test_slots_are_round_one_oldest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    slots = await store.poll_slots(poll.id, 1)
    assert [(s.poll_id, s.round_number, s.start, s.end) for s in slots] == [
        (poll.id, 1, 5000.0, 5000.0 + 4 * HOUR),
        (poll.id, 1, 9000.0, 9000.0 + 4 * HOUR),
    ]
    assert await store.poll_slots(poll.id, 2) == []


async def test_gm_is_auto_joined(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    assert await store.players(poll.id) == [GM]


async def test_join_twice_returns_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    assert await store.join(poll.id, PLAYER) is True
    assert await store.join(poll.id, PLAYER) is False
    assert await store.join(poll.id, GM) is False
    assert await store.players(poll.id) == [GM, PLAYER]


async def test_join_beyond_max_players(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    for user_id in range(100, 100 + MAX_PLAYERS - 1):
        assert await store.join(poll.id, user_id) is True
    assert len(await store.players(poll.id)) == MAX_PLAYERS
    assert await store.join(poll.id, 999) is False
    assert 999 not in await store.players(poll.id)


async def test_leave_removes_player_and_votes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.join(poll.id, PLAYER)
    [slot, _] = await store.poll_slots(poll.id, 1)
    path = tmp_path / "sessions.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executemany(
            "INSERT INTO poll_votes (poll_id, user_id, slot_id, vote) VALUES (?, ?, ?, 'yes')",
            [(poll.id, PLAYER, slot.id), (poll.id, GM, slot.id)],
        )

    await store.leave(poll.id, PLAYER)
    await store.leave(poll.id, PLAYER)

    assert await store.players(poll.id) == [GM]
    with closing(sqlite3.connect(path)) as conn:
        voters = conn.execute("SELECT user_id FROM poll_votes").fetchall()
    assert voters == [(GM,)]


async def test_open_polls_filters_status_and_guild(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = await _create(store, now=1000.0)
    second = await _create(store, now=2000.0)
    confirmed = await _create(store, now=3000.0)
    failed = await _create(store, now=4000.0)
    cancelled = await _create(store, now=5000.0)
    await _create(store, guild_id=OTHER_GUILD)
    await store.set_status(first.id, STATUS_AWAITING_CONFIRM)
    await store.set_status(confirmed.id, STATUS_CONFIRMED)
    await store.set_status(failed.id, STATUS_FAILED)
    await store.set_status(cancelled.id, STATUS_CANCELLED)

    assert [p.id for p in await store.open_polls(GUILD)] == [first.id, second.id]
    [awaiting, _] = await store.open_polls(GUILD)
    assert awaiting.status == STATUS_AWAITING_CONFIRM


async def test_set_status_rejects_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    with pytest.raises(ValueError):
        await store.set_status(poll.id, "bogus")


async def test_second_store_sees_data(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.join(poll.id, PLAYER)

    restarted = _store(tmp_path)
    assert await restarted.get_poll(poll.id) == poll
    assert await restarted.players(poll.id) == [GM, PLAYER]
    assert len(await restarted.poll_slots(poll.id, 1)) == 2


async def test_creates_missing_directory(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "nested" / "sessions.sqlite3")
    await _create(store)
    assert (tmp_path / "nested" / "sessions.sqlite3").exists()
