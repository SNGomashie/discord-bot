from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from ninja_bot.sessions.scoring import MAX_PLAYERS, Vote
from ninja_bot.sessions.slots import Slot
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_VOTING,
    Poll,
    Session,
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
    await store.record_votes(poll.id, PLAYER, {slot.id: Vote.YES})
    await store.record_votes(poll.id, GM, {slot.id: Vote.YES})

    await store.leave(poll.id, PLAYER)
    await store.leave(poll.id, PLAYER)

    assert await store.players(poll.id) == [GM]
    assert await store.votes(poll.id, 1) == {(GM, slot.id): Vote.YES}


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


async def test_voting_polls_spans_guilds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = await _create(store, now=1000.0)
    awaiting = await _create(store, now=2000.0)
    elsewhere = await _create(store, guild_id=OTHER_GUILD, now=3000.0)
    await store.set_status(awaiting.id, STATUS_AWAITING_CONFIRM)

    assert [p.id for p in await store.voting_polls()] == [first.id, elsewhere.id]


async def test_set_status_rejects_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    with pytest.raises(ValueError):
        await store.set_status(poll.id, "bogus")


async def test_second_store_sees_data(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.join(poll.id, PLAYER)
    [slot, _] = await store.poll_slots(poll.id, 1)
    await store.record_votes(poll.id, PLAYER, {slot.id: Vote.IF_NEEDED})
    await store.record_nudge(poll.id, 1, 1500.0)
    other = await _create(store)
    [other_slot, _] = await store.poll_slots(other.id, 1)
    session = await store.confirm(other.id, other_slot.id, [GM])
    await store.mark_reminder(session.id, 3600)

    restarted = _store(tmp_path)
    assert await restarted.get_poll(poll.id) == poll
    assert await restarted.players(poll.id) == [GM, PLAYER]
    assert len(await restarted.poll_slots(poll.id, 1)) == 2
    assert await restarted.votes(poll.id, 1) == {(PLAYER, slot.id): Vote.IF_NEEDED}
    assert await restarted.nudge_count(poll.id, 1) == 1
    assert await restarted.last_nudge(poll.id, 1) == 1500.0
    assert await restarted.get_session(session.id) == session
    assert await restarted.reminders_sent(session.id) == {3600}


async def test_creates_missing_directory(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "nested" / "sessions.sqlite3")
    await _create(store)
    assert (tmp_path / "nested" / "sessions.sqlite3").exists()


async def test_record_votes_replaces_revote(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    [first, second] = await store.poll_slots(poll.id, 1)
    await store.record_votes(poll.id, PLAYER, {first.id: Vote.YES, second.id: Vote.NO})
    await store.record_votes(poll.id, PLAYER, {first.id: Vote.NO})
    await store.record_votes(poll.id, PLAYER, {})

    assert await store.votes(poll.id, 1) == {
        (PLAYER, first.id): Vote.NO,
        (PLAYER, second.id): Vote.NO,
    }


async def test_votes_limited_to_round_and_poll(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    other = await _create(store)
    [old, _] = await store.poll_slots(poll.id, 1)
    [other_slot, _] = await store.poll_slots(other.id, 1)
    await store.record_votes(poll.id, PLAYER, {old.id: Vote.YES})
    await store.record_votes(other.id, PLAYER, {other_slot.id: Vote.YES})

    await store.advance_round(poll.id, (Slot(20_000.0, 30_000.0),), date(2026, 11, 20), 2000.0)
    [new] = await store.poll_slots(poll.id, 2)
    await store.record_votes(poll.id, GM, {new.id: Vote.IF_NEEDED})

    assert await store.votes(poll.id, 1) == {(PLAYER, old.id): Vote.YES}
    assert await store.votes(poll.id, 2) == {(GM, new.id): Vote.IF_NEEDED}
    assert await store.votes(other.id, 1) == {(PLAYER, other_slot.id): Vote.YES}


async def test_advance_round_increments_and_resets_nudges(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.set_poll_message(poll.id, 555)
    await store.set_status(poll.id, STATUS_AWAITING_CONFIRM)
    await store.record_nudge(poll.id, 1, 1500.0)
    await store.record_nudge(poll.id, 1, 1600.0)

    advanced = await store.advance_round(
        poll.id, (Slot(20_000.0, 30_000.0),), date(2026, 11, 20), 2000.0
    )

    assert advanced.round_number == 2
    assert advanced.round_started_at == 2000.0
    assert advanced.window_start == date(2026, 11, 20)
    assert advanced.status == STATUS_VOTING
    assert advanced.message_id == 555
    assert advanced.created_at == poll.created_at
    assert await store.get_poll(poll.id) == advanced
    assert [(s.round_number, s.start, s.end) for s in await store.poll_slots(poll.id, 2)] == [
        (2, 20_000.0, 30_000.0)
    ]
    assert len(await store.poll_slots(poll.id, 1)) == 2
    assert await store.nudge_count(poll.id, 2) == 0
    assert await store.last_nudge(poll.id, 2) is None
    assert await store.nudge_count(poll.id, 1) == 2

    third = await store.advance_round(poll.id, (), date(2026, 12, 4), 3000.0, message_id=777)
    assert third.round_number == 3
    assert third.message_id == 777


async def test_advance_round_unknown_poll(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        await store.advance_round(1, (Slot(1.0, 2.0),), date(2026, 11, 20), 2000.0)


async def test_confirm_stores_roster_and_flips_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.set_poll_message(poll.id, 555)
    [_, later] = await store.poll_slots(poll.id, 1)

    session = await store.confirm(poll.id, later.id, [PLAYER, GM])

    assert session == Session(
        id=session.id,
        poll_id=poll.id,
        guild_id=GUILD,
        channel_id=CHANNEL,
        message_id=555,
        name="Curse of Strahd",
        gm_id=GM,
        start=later.start,
        end=later.end,
        roster=(PLAYER, GM),
        cancelled=False,
    )
    assert await store.get_session(session.id) == session
    assert await store.get_session(session.id + 1) is None
    fetched = await store.get_poll(poll.id)
    assert fetched is not None
    assert fetched.status == STATUS_CONFIRMED


async def test_confirm_empty_roster_and_unknown_slot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    other = await _create(store)
    [slot, _] = await store.poll_slots(poll.id, 1)
    [other_slot, _] = await store.poll_slots(other.id, 1)

    with pytest.raises(ValueError):
        await store.confirm(poll.id, other_slot.id, [GM])
    fetched = await store.get_poll(poll.id)
    assert fetched is not None
    assert fetched.status == STATUS_VOTING

    session = await store.confirm(poll.id, slot.id, [])
    assert session.roster == ()
    assert await store.get_session(session.id) == session


async def test_cancel_poll(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    await store.cancel_poll(poll.id)
    fetched = await store.get_poll(poll.id)
    assert fetched is not None
    assert fetched.status == STATUS_CANCELLED
    assert await store.open_polls(GUILD) == []


async def test_upcoming_and_active_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    [early, late] = await store.poll_slots(poll.id, 1)
    later = await store.confirm(poll.id, late.id, [GM])
    sooner = await store.confirm(poll.id, early.id, [GM])
    other_poll = await _create(store, guild_id=OTHER_GUILD)
    [other_slot, _] = await store.poll_slots(other_poll.id, 1)
    elsewhere = await store.confirm(other_poll.id, other_slot.id, [GM])

    assert await store.upcoming_sessions(GUILD, 0.0) == [sooner, later]
    # `early` (5000-19400) has started but not ended.
    assert await store.upcoming_sessions(GUILD, 6000.0) == [later]
    assert await store.active_sessions(6000.0) == [sooner, elsewhere, later]
    assert await store.active_sessions(early.end) == [later]
    assert await store.active_sessions(late.end) == []


async def test_cancel_session_hides_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    [slot, other_slot] = await store.poll_slots(poll.id, 1)
    session = await store.confirm(poll.id, slot.id, [GM])
    kept = await store.confirm(poll.id, other_slot.id, [GM])

    await store.cancel_session(session.id)

    assert await store.upcoming_sessions(GUILD, 0.0) == [kept]
    assert await store.active_sessions(0.0) == [kept]
    fetched = await store.get_session(session.id)
    assert fetched is not None
    assert fetched.cancelled is True


async def test_mark_reminder_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    [slot, other_slot] = await store.poll_slots(poll.id, 1)
    session = await store.confirm(poll.id, slot.id, [GM])
    other = await store.confirm(poll.id, other_slot.id, [GM])

    assert await store.reminders_sent(session.id) == set()
    await store.mark_reminder(session.id, 86_400)
    await store.mark_reminder(session.id, 86_400)
    await store.mark_reminder(session.id, 3_600)

    assert await store.reminders_sent(session.id) == {86_400, 3_600}
    assert await store.reminders_sent(other.id) == set()


async def test_nudge_counters_per_round(tmp_path: Path) -> None:
    store = _store(tmp_path)
    poll = await _create(store)
    other = await _create(store)
    assert await store.nudge_count(poll.id, 1) == 0
    assert await store.last_nudge(poll.id, 1) is None

    await store.record_nudge(poll.id, 1, 1500.0)
    await store.record_nudge(poll.id, 1, 1400.0)
    await store.record_nudge(poll.id, 2, 9000.0)
    await store.record_nudge(other.id, 1, 1700.0)

    assert await store.nudge_count(poll.id, 1) == 2
    assert await store.last_nudge(poll.id, 1) == 1500.0
    assert await store.nudge_count(poll.id, 2) == 1
    assert await store.last_nudge(poll.id, 2) == 9000.0
    assert await store.nudge_count(other.id, 1) == 1
