"""The session poll background loop: deadline closes, reminders and nudges."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from ninja_bot.cogs.sessions import NOTIFY_CHECK_SECONDS, Sessions
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.sessions import content
from ninja_bot.sessions.reminders import MAX_NUDGES, NUDGE_INTERVAL_SECONDS
from ninja_bot.sessions.scoring import Vote
from ninja_bot.sessions.service import NoPrefill, RoundAdvanced, RoundFits, SessionService
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_VOTING,
    RoundState,
    Session,
    SqliteSessionStore,
)

TZ = ZoneInfo("Europe/Amsterdam")
GUILD = 10
CHANNEL = 100
GM = 1
PLAYER = 2
POLL_MESSAGE = 555
HOUR = 3600
DAY = 86_400


class FakeClock:
    def __init__(self) -> None:
        # Monday 2026-11-02 12:00 UTC.
        self.now = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()

    def __call__(self) -> float:
        return self.now


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    channel.guild = MagicMock()
    channel.permissions_for.return_value = discord.Permissions.all()
    channel.send = AsyncMock(return_value=MagicMock(id=777))
    channel.get_partial_message.return_value.edit = AsyncMock()
    return channel


def make_bot(channel: MagicMock | None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
    )
    # The close path posts through a partial messageable of the same channel.
    bot.get_partial_messageable.return_value = channel if channel is not None else make_channel()
    bot.wait_until_ready = AsyncMock()
    return bot


@pytest.fixture
def store(tmp_path: Path) -> SqliteSessionStore:
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def channel() -> MagicMock:
    return make_channel()


def make_cog(store: SqliteSessionStore, clock: FakeClock, bot: MagicMock) -> Sessions:
    service = SessionService(store, TZ, clock, NoPrefill())
    return Sessions(bot, service, UserCooldown(30, clock))


@pytest.fixture
def cog(store: SqliteSessionStore, clock: FakeClock, channel: MagicMock) -> Sessions:
    return make_cog(store, clock, make_bot(channel))


async def open_poll(cog: Sessions, **kwargs: Any) -> int:
    poll = await cog.service.create_poll(GUILD, CHANNEL, GM, "Strahd", "fri,sat", "19:00", **kwargs)
    await cog.service.store.set_poll_message(poll.id, POLL_MESSAGE)
    await cog.service.join(poll.id, PLAYER)
    return poll.id


async def slot_ids(cog: Sessions, poll_id: int) -> list[int]:
    return [slot.id for slot in await cog.service.store.poll_slots(poll_id, 1)]


async def confirmed_session(cog: Sessions) -> Session:
    poll_id = await open_poll(cog)
    slots = await slot_ids(cog, poll_id)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    await cog.service.submit_vote(poll_id, PLAYER, slots, [])
    assert isinstance(await cog.service.close_round(poll_id, GM, False, "manual"), RoundFits)
    return await cog.service.confirm(poll_id, slots[0], GM, False)


def sent(channel: MagicMock) -> list[dict[str, Any]]:
    return [call.kwargs for call in channel.send.await_args_list]


def pinged(kwargs: dict[str, Any]) -> list[int]:
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert mentions.roles is False
    users = mentions.users
    return [] if users is False else [user.id for user in users]


# Reminders


async def test_24h_and_1h_reminders_are_posted_once_each(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    session = await confirmed_session(cog)
    clock.now = session.start - DAY - 60
    await cog.run_notify_cycle()
    assert sent(channel) == []

    clock.now = session.start - DAY
    await cog.run_notify_cycle()
    await cog.run_notify_cycle()
    (first,) = sent(channel)
    assert first["content"] == content.REMINDER_MESSAGE.format(
        players=f"<@{GM}>, <@{PLAYER}>", name="Strahd", start=f"<t:{int(session.start)}:R>"
    )
    assert pinged(first) == [GM, PLAYER]
    assert first["embed"].title is not None and "Strahd" in first["embed"].title

    clock.now = session.start - HOUR + 1
    await cog.run_notify_cycle()
    await cog.run_notify_cycle()
    assert len(sent(channel)) == 2

    clock.now = session.start + 60
    await cog.run_notify_cycle()
    assert len(sent(channel)) == 2


async def test_restart_does_not_repeat_a_reminder(
    store: SqliteSessionStore, clock: FakeClock, channel: MagicMock
) -> None:
    cog = make_cog(store, clock, make_bot(channel))
    session = await confirmed_session(cog)
    clock.now = session.start - DAY + 10
    await cog.run_notify_cycle()
    restarted = make_cog(store, clock, make_bot(channel))
    await restarted.run_notify_cycle()
    assert len(sent(channel)) == 1


async def test_bot_down_catch_up_posts_one_reminder(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    session = await confirmed_session(cog)
    # Down across the 24 h mark: only the 1 h reminder is posted, the 24 h one is skipped.
    clock.now = session.start - HOUR / 2
    await cog.run_notify_cycle()
    await cog.run_notify_cycle()
    assert len(sent(channel)) == 1
    assert await cog.service.store.reminders_sent(session.id) == {DAY, HOUR}


async def test_cancelled_session_is_silent(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    session = await confirmed_session(cog)
    await cog.service.cancel_session(session.id, GM, False)
    clock.now = session.start - HOUR
    await cog.run_notify_cycle()
    assert sent(channel) == []


async def test_forbidden_send_is_logged_and_not_retried(
    cog: Sessions, clock: FakeClock, channel: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    session = await confirmed_session(cog)
    channel.send.side_effect = discord.Forbidden(MagicMock(status=403), "Missing Access")
    clock.now = session.start - DAY
    with caplog.at_level(logging.WARNING):
        await cog.run_notify_cycle()
        await cog.run_notify_cycle()
    channel.send.assert_awaited_once()
    assert "Could not send session reminder" in caplog.text


async def test_missing_channel_skips_and_warns_once(
    store: SqliteSessionStore, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    cog = make_cog(store, clock, make_bot(make_channel()))
    session = await confirmed_session(cog)
    bot = make_bot(None)
    gone = make_cog(store, clock, bot)
    clock.now = session.start - DAY
    with caplog.at_level(logging.WARNING):
        await gone.run_notify_cycle()
        await gone.run_notify_cycle()
    assert caplog.text.count(f"channel {CHANNEL}") == 1
    # Not marked, so it is posted once the channel works again.
    assert await store.reminders_sent(session.id) == set()


# Nudges


async def test_nudges_follow_the_cadence_and_cap(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    poll_id = await open_poll(cog)
    await cog.service.submit_vote(poll_id, GM, await slot_ids(cog, poll_id), [])
    start = clock.now
    clock.now = start + NUDGE_INTERVAL_SECONDS - 1
    await cog.run_notify_cycle()
    assert sent(channel) == []

    for nudge in range(1, MAX_NUDGES + 2):
        clock.now = start + nudge * NUDGE_INTERVAL_SECONDS
        await cog.run_notify_cycle()
        await cog.run_notify_cycle()
        assert len(sent(channel)) == min(nudge, MAX_NUDGES)

    kwargs = sent(channel)[0]
    assert kwargs["content"] == content.NUDGE_MESSAGE.format(
        players=f"<@{PLAYER}>", name="Strahd", round=1
    )
    assert pinged(kwargs) == [PLAYER]
    reference = kwargs["reference"]
    assert reference.message_id == POLL_MESSAGE
    assert reference.fail_if_not_exists is False


async def test_cancelled_poll_is_never_nudged(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    poll_id = await open_poll(cog)
    await cog.service.cancel_poll(poll_id, GM, False)
    clock.now += 2 * NUDGE_INTERVAL_SECONDS
    await cog.run_notify_cycle()
    assert sent(channel) == []


class CountingStore(SqliteSessionStore):
    """Records the poll id of every round read (`round_state` or its single-table parts)."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.reads: list[tuple[str, int]] = []

    async def round_state(self, poll_id: int, round_number: int) -> RoundState:
        self.reads.append(("round_state", poll_id))
        return await super().round_state(poll_id, round_number)

    async def players(self, poll_id: int) -> list[int]:
        self.reads.append(("players", poll_id))
        return await super().players(poll_id)

    async def votes(self, poll_id: int, round_number: int) -> dict[tuple[int, int], Vote]:
        self.reads.append(("votes", poll_id))
        return await super().votes(poll_id, round_number)


async def test_cycle_reads_each_round_once(
    tmp_path: Path, clock: FakeClock, channel: MagicMock
) -> None:
    store = CountingStore(tmp_path / "sessions.sqlite3")
    cog = make_cog(store, clock, make_bot(channel))
    polls = [await open_poll(cog) for _ in range(3)]
    clock.now += NUDGE_INTERVAL_SECONDS
    store.reads.clear()

    await cog.run_notify_cycle()

    assert sorted(store.reads) == [("round_state", poll_id) for poll_id in polls]
    assert len(sent(channel)) == len(polls)  # every poll nudged


async def test_poll_closed_by_the_deadline_is_not_nudged(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    # The deadline and the first nudge fall in the same cycle.
    poll_id = await open_poll(cog, round_hours=24, min_players=2)
    await cog.service.submit_vote(poll_id, GM, await slot_ids(cog, poll_id), [])
    clock.now += NUDGE_INTERVAL_SECONDS

    await cog.run_notify_cycle()

    poll = await cog.service.store.get_poll(poll_id)
    assert poll is not None and poll.round_number == 2
    # Only the next-round message, no nudge reply to the old poll message.
    [kwargs] = sent(channel)
    assert "reference" not in kwargs


# Deadlines


async def test_fully_voted_round_is_closed_by_the_loop(cog: Sessions, channel: MagicMock) -> None:
    # Normally closed on Save; the loop catches rounds a crash left open.
    poll_id = await open_poll(cog, round_hours=12)
    slots = await slot_ids(cog, poll_id)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    await cog.service.submit_vote(poll_id, PLAYER, [slots[0]], [])
    await cog.run_notify_cycle()
    poll = await cog.service.store.get_poll(poll_id)
    assert poll is not None and poll.status == STATUS_AWAITING_CONFIRM
    assert pinged(sent(channel)[0]) == [GM]


async def test_deadline_with_pending_players_closes_the_round(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    poll_id = await open_poll(cog, round_hours=12, min_players=2)
    slots = await slot_ids(cog, poll_id)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    clock.now += 12 * HOUR - 1
    await cog.run_notify_cycle()
    store = cog.service.store
    assert (await store.get_poll(poll_id)).status == STATUS_VOTING  # type: ignore[union-attr]
    assert sent(channel) == []

    clock.now += 1
    await cog.run_notify_cycle()
    poll = await store.get_poll(poll_id)
    assert poll is not None
    # Only the GM voted and 2 players are needed: the poll moves on to round 2.
    assert (poll.status, poll.round_number) == (STATUS_VOTING, 2)
    channel.get_partial_message.return_value.edit.assert_awaited()
    assert len(sent(channel)) == 1


# Repairing unposted outcome messages

FORBIDDEN = discord.Forbidden(MagicMock(status=403), "Missing Access")


async def voted_poll(cog: Sessions, *, player_votes: bool) -> int:
    poll_id = await open_poll(cog)
    slots = await slot_ids(cog, poll_id)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    if player_votes:
        await cog.service.submit_vote(poll_id, PLAYER, slots, [])
    return poll_id


async def test_failed_result_post_is_repaired_once(cog: Sessions, channel: MagicMock) -> None:
    poll_id = await voted_poll(cog, player_votes=True)
    channel.send.side_effect = [FORBIDDEN, MagicMock(id=777)]
    assert isinstance(await cog.close_and_post(poll_id, "manual", GM), RoundFits)
    store = cog.service.store
    poll = await store.get_poll(poll_id)
    assert poll is not None
    assert (poll.status, poll.result_message_id) == (STATUS_AWAITING_CONFIRM, None)

    await cog.run_notify_cycle()
    await cog.run_notify_cycle()
    assert channel.send.await_count == 2
    kwargs = sent(channel)[1]
    assert kwargs["content"] == content.RESULT_PING.format(gm=f"<@{GM}>")
    assert pinged(kwargs) == [GM]
    assert kwargs["view"].children
    assert (await store.get_poll(poll_id)).result_message_id == 777  # type: ignore[union-attr]


async def test_failed_next_round_post_is_repaired_once(cog: Sessions, channel: MagicMock) -> None:
    poll_id = await voted_poll(cog, player_votes=False)
    channel.send.side_effect = [FORBIDDEN, MagicMock(id=777)]
    assert isinstance(await cog.close_and_post(poll_id, "manual", GM), RoundAdvanced)
    store = cog.service.store
    poll = await store.get_poll(poll_id)
    assert poll is not None
    assert (poll.round_number, poll.message_id) == (2, None)

    await cog.run_notify_cycle()
    await cog.run_notify_cycle()
    assert channel.send.await_count == 2
    kwargs = sent(channel)[1]
    assert len(kwargs["embeds"]) == 2
    assert kwargs["view"].children
    # Nobody voted in round 2 yet.
    assert pinged(kwargs) == [GM, PLAYER]
    assert (await store.get_poll(poll_id)).message_id == 777  # type: ignore[union-attr]


async def test_posted_outcomes_are_not_repaired(cog: Sessions, channel: MagicMock) -> None:
    fits = await voted_poll(cog, player_votes=True)
    advances = await voted_poll(cog, player_votes=False)
    await cog.close_and_post(fits, "manual", GM)
    await cog.close_and_post(advances, "manual", GM)
    assert channel.send.await_count == 2
    await cog.run_notify_cycle()
    assert channel.send.await_count == 2


async def test_repair_waits_for_a_usable_channel(
    store: SqliteSessionStore, clock: FakeClock
) -> None:
    bot = make_bot(None)
    gone = make_cog(store, clock, bot)
    poll_id = await voted_poll(gone, player_votes=True)
    send = bot.get_partial_messageable.return_value.send
    send.side_effect = FORBIDDEN
    await gone.close_and_post(poll_id, "manual", GM)
    await gone.run_notify_cycle()
    send.assert_awaited_once()
    assert (await store.get_poll(poll_id)).result_message_id is None  # type: ignore[union-attr]

    channel = make_channel()
    working = make_cog(store, clock, make_bot(channel))
    await working.run_notify_cycle()
    channel.send.assert_awaited_once()
    assert (await store.get_poll(poll_id)).result_message_id == 777  # type: ignore[union-attr]


async def test_the_lock_serialises_a_close_and_a_repair(cog: Sessions, channel: MagicMock) -> None:
    poll_id = await voted_poll(cog, player_votes=True)
    release = asyncio.Event()
    sending = asyncio.Event()

    async def slow_send(**kwargs: Any) -> MagicMock:
        sending.set()
        await release.wait()
        return MagicMock(id=777)

    channel.send.side_effect = slow_send
    close = asyncio.create_task(cog.close_and_post(poll_id, "manual", GM))
    await sending.wait()
    # The round is closed but its result isn't stored yet: a repair now would post it again.
    repair = asyncio.create_task(cog.repair_missing_messages())
    await asyncio.wait({repair}, timeout=0.1)
    assert not repair.done()
    release.set()
    await close
    await repair
    channel.send.assert_awaited_once()


async def test_no_nudge_without_a_poll_message(
    cog: Sessions, clock: FakeClock, channel: MagicMock
) -> None:
    poll = await cog.service.create_poll(GUILD, CHANNEL, GM, "Strahd", "fri,sat", "19:00")
    await cog.service.join(poll.id, PLAYER)
    await cog.service.submit_vote(poll.id, GM, await slot_ids(cog, poll.id), [])
    clock.now += 2 * NUDGE_INTERVAL_SECONDS
    await cog.run_notify_cycle()
    assert sent(channel) == []


# The loop


async def test_the_cycle_never_raises(
    cog: Sessions, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(cog.service.store, "polls_missing_messages", broken)
    monkeypatch.setattr(cog.service.store, "voting_polls", broken)
    monkeypatch.setattr(cog.service.store, "active_sessions", broken)
    with caplog.at_level(logging.ERROR):
        await cog.run_notify_cycle()
    assert caplog.text.count("notify cycle") == 4


async def test_loop_starts_on_load_and_stops_on_unload(cog: Sessions) -> None:
    bot: Any = cog.bot
    bot.wait_until_ready = AsyncMock(side_effect=asyncio.Event().wait)
    await cog.cog_load()
    assert cog.notify_loop.is_running()
    assert cog.notify_loop.seconds == NOTIFY_CHECK_SECONDS
    await asyncio.sleep(0)
    bot.wait_until_ready.assert_awaited_once()
    await cog.cog_unload()
    await asyncio.sleep(0)
    assert not cog.notify_loop.is_running()
