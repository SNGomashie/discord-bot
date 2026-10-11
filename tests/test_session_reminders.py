from __future__ import annotations

import ast
from datetime import date, time
from pathlib import Path

from ninja_bot.sessions import reminders
from ninja_bot.sessions.reminders import (
    MAX_NUDGES,
    NUDGE_INTERVAL_SECONDS,
    REMINDER_OFFSETS,
    SessionNotifier,
    due_nudge,
    due_reminder,
    skipped_offsets,
)
from ninja_bot.sessions.scoring import Vote
from ninja_bot.sessions.slots import Slot
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_VOTING,
    Poll,
    Session,
    SqliteSessionStore,
)

DAY = 86_400
HOUR = 3_600
START = 1_000_000.0
GM = 1
PLAYER = 2
OTHER = 3


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _session(start: float = START) -> Session:
    return Session(
        id=1,
        poll_id=1,
        guild_id=10,
        channel_id=100,
        message_id=None,
        name="Curse of Strahd",
        gm_id=GM,
        start=start,
        end=start + 4 * HOUR,
        roster=(GM, PLAYER),
        cancelled=False,
    )


def _poll(status: str = STATUS_VOTING, round_started_at: float = 0.0) -> Poll:
    return Poll(
        id=1,
        guild_id=10,
        channel_id=100,
        message_id=None,
        name="Curse of Strahd",
        gm_id=GM,
        days=(4,),
        times=(time(19, 0),),
        weeks=2,
        duration_hours=4.0,
        min_players=0,
        round_hours=0.0,
        round_number=1,
        window_start=date(2026, 11, 6),
        status=status,
        created_at=round_started_at,
        round_started_at=round_started_at,
    )


def test_constants() -> None:
    assert REMINDER_OFFSETS == (86_400, 3_600)
    assert NUDGE_INTERVAL_SECONDS == 86_400
    assert MAX_NUDGES == 3


def test_nothing_due_before_24h() -> None:
    assert due_reminder(_session(), START - DAY - 1, set()) is None


def test_24h_due_exactly_at_boundary() -> None:
    assert due_reminder(_session(), START - DAY, set()) == DAY


def test_24h_not_repeated_once_sent() -> None:
    assert due_reminder(_session(), START - DAY + 10, {DAY}) is None


def test_1h_boundary() -> None:
    session = _session()
    assert due_reminder(session, START - HOUR - 1, {DAY}) is None
    assert due_reminder(session, START - HOUR, {DAY}) == HOUR


def test_bot_down_across_24h_point_posts_only_1h() -> None:
    session = _session()
    now = START - HOUR + 60
    assert due_reminder(session, now, set()) == HOUR
    assert skipped_offsets(session, now, set()) == [DAY]


def test_late_24h_still_posted_before_1h_point() -> None:
    session = _session()
    now = START - 2 * HOUR
    assert due_reminder(session, now, set()) == DAY
    assert skipped_offsets(session, now, set()) == []


def test_nothing_due_once_started() -> None:
    session = _session()
    for now in (START, START + 1, START + DAY):
        assert due_reminder(session, now, set()) is None
        assert skipped_offsets(session, now, set()) == []
    assert due_reminder(session, START - 1, set()) == HOUR


def test_nudge_after_interval_from_round_start() -> None:
    poll = _poll(round_started_at=1000.0)
    assert not due_nudge(poll, [PLAYER], 0, None, 1000.0 + DAY - 1)
    assert due_nudge(poll, [PLAYER], 0, None, 1000.0 + DAY)


def test_nudge_spacing_from_last_nudge() -> None:
    poll = _poll(round_started_at=0.0)
    last = 5 * DAY
    assert not due_nudge(poll, [PLAYER], 1, last, last + DAY - 1)
    assert due_nudge(poll, [PLAYER], 1, last, last + DAY)


def test_nudge_cap() -> None:
    poll = _poll()
    assert due_nudge(poll, [PLAYER], MAX_NUDGES - 1, DAY, 10 * DAY)
    assert not due_nudge(poll, [PLAYER], MAX_NUDGES, DAY, 10 * DAY)


def test_nudge_needs_pending_players() -> None:
    assert not due_nudge(_poll(), [], 0, None, 10 * DAY)


def test_nudge_only_while_voting() -> None:
    assert not due_nudge(_poll(status=STATUS_AWAITING_CONFIRM), [PLAYER], 0, None, 10 * DAY)


def test_no_discord_import() -> None:
    tree = ast.parse(Path(reminders.__file__).read_text(encoding="utf-8"))
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.split(".")[0] == "discord" for name in names)


async def _poll_in_store(store: SqliteSessionStore, now: float) -> Poll:
    return await store.create_poll(
        guild_id=10,
        channel_id=100,
        name="Curse of Strahd",
        gm_id=GM,
        days=(4,),
        times=(time(19, 0),),
        weeks=2,
        duration_hours=4.0,
        min_players=0,
        round_hours=0.0,
        window_start=date(2026, 11, 6),
        slots=(Slot(START, START + 4 * HOUR), Slot(START + DAY, START + DAY + 4 * HOUR)),
        now=now,
    )


async def test_notifier_reminders_persist_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    store = SqliteSessionStore(path)
    poll = await _poll_in_store(store, 0.0)
    [slot, _] = await store.poll_slots(poll.id, 1)
    session = await store.confirm(poll.id, slot.id, [GM])
    clock = FakeClock(START - DAY)
    notifier = SessionNotifier(store, clock)

    assert await notifier.due_reminders() == [(session, DAY)]
    await notifier.confirm_reminder(session.id, DAY)
    assert await notifier.due_reminders() == []

    restarted = SessionNotifier(SqliteSessionStore(path), clock)
    assert await restarted.due_reminders() == []
    clock.now = START - HOUR
    assert await restarted.due_reminders() == [(session, HOUR)]
    await restarted.confirm_reminder(session.id, HOUR)
    clock.now = START + 1
    assert await restarted.due_reminders() == []


async def test_notifier_marks_skipped_reminders(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    poll = await _poll_in_store(store, 0.0)
    [slot, _] = await store.poll_slots(poll.id, 1)
    session = await store.confirm(poll.id, slot.id, [GM])
    notifier = SessionNotifier(store, FakeClock(START - 30 * 60))

    assert await notifier.due_reminders() == [(session, HOUR)]
    assert await store.reminders_sent(session.id) == {DAY}


async def test_notifier_skips_cancelled_sessions(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    poll = await _poll_in_store(store, 0.0)
    [slot, _] = await store.poll_slots(poll.id, 1)
    session = await store.confirm(poll.id, slot.id, [GM])
    await store.cancel_session(session.id)
    assert await SessionNotifier(store, FakeClock(START - HOUR)).due_reminders() == []


async def test_notifier_nudges_pending_players(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    store = SqliteSessionStore(path)
    poll = await _poll_in_store(store, 0.0)
    await store.join(poll.id, PLAYER)
    await store.join(poll.id, OTHER)
    [slot, _] = await store.poll_slots(poll.id, 1)
    await store.record_votes(poll.id, PLAYER, {slot.id: Vote.YES})
    clock = FakeClock(DAY - 1)
    notifier = SessionNotifier(store, clock)

    assert await notifier.due_nudges() == []
    clock.now = DAY
    assert await notifier.due_nudges() == [(poll, [GM, OTHER])]
    await notifier.confirm_nudge(poll.id, poll.round_number)
    assert await notifier.due_nudges() == []

    restarted = SessionNotifier(SqliteSessionStore(path), clock)
    clock.now = 2 * DAY - 1
    assert await restarted.due_nudges() == []
    clock.now = 2 * DAY
    assert await restarted.due_nudges() == [(poll, [GM, OTHER])]


async def test_notifier_nudge_cap_and_status(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    poll = await _poll_in_store(store, 0.0)
    clock = FakeClock()
    notifier = SessionNotifier(store, clock)
    for n in range(1, MAX_NUDGES + 1):
        clock.now = n * DAY
        assert await notifier.due_nudges() == [(poll, [GM])]
        await notifier.confirm_nudge(poll.id, poll.round_number)
    clock.now = 10 * DAY
    assert await notifier.due_nudges() == []

    fresh = await _poll_in_store(store, 0.0)
    await store.set_status(fresh.id, STATUS_AWAITING_CONFIRM)
    assert await notifier.due_nudges() == []


async def test_notifier_nothing_when_everyone_voted(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    poll = await _poll_in_store(store, 0.0)
    [slot, _] = await store.poll_slots(poll.id, 1)
    await store.record_votes(poll.id, GM, {slot.id: Vote.NO})
    assert await SessionNotifier(store, FakeClock(5 * DAY)).due_nudges() == []
