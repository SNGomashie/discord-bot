import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from ninja_bot.activity.store import Session, SqliteActivityStore
from ninja_bot.cogs.activity import Activity, members_in_voice

GUILD_ID = 1
AFK = 99


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail = fail

    def _record(self, *call: Any) -> None:
        self.calls.append(call)
        if self.fail:
            raise RuntimeError("disk full")

    async def open_session(self, user_id: int, channel_id: int, now: float) -> None:
        self._record("open", user_id, channel_id, now)

    async def close_session(self, user_id: int, now: float) -> None:
        self._record("close", user_id, now)

    async def heartbeat(self, now: float) -> None:
        self._record("heartbeat", now)

    async def close_stale(self, default_end: float | None = None) -> int:
        self._record("close_stale")
        return 0


def make_guild(
    guild_id: int = GUILD_ID, afk: int | None = AFK, voice: dict[int, list[Any]] | None = None
) -> Any:
    channels = [SimpleNamespace(id=cid, members=members) for cid, members in (voice or {}).items()]
    return SimpleNamespace(
        id=guild_id,
        afk_channel=None if afk is None else SimpleNamespace(id=afk),
        voice_channels=channels,
        stage_channels=[],
    )


def make_member(user_id: int, guild: Any = None, bot: bool = False) -> Any:
    return SimpleNamespace(id=user_id, bot=bot, guild=guild or make_guild())


def state(channel: int | None) -> Any:
    return SimpleNamespace(channel=None if channel is None else SimpleNamespace(id=channel))


def make_bot(*guilds: Any) -> MagicMock:
    bot = MagicMock()
    bot.guilds = list(guilds)
    bot.get_guild = lambda guild_id: next((g for g in guilds if g.id == guild_id), None)
    return bot


def make_cog(store: Any, *guilds: Any, guild_id: int | None = GUILD_ID) -> Activity:
    return Activity(make_bot(*guilds), store, guild_id, clock=FakeClock())


async def update(cog: Activity, member: Any, before: int | None, after: int | None) -> None:
    await cog.on_voice_state_update(member, state(before), state(after))


async def test_join_opens_a_session() -> None:
    store = FakeStore()
    await update(make_cog(store), make_member(7), None, 1)
    assert store.calls == [("open", 7, 1, 1000.0)]


async def test_leave_closes_the_session() -> None:
    store = FakeStore()
    await update(make_cog(store), make_member(7), 1, None)
    assert store.calls == [("close", 7, 1000.0)]


async def test_move_reopens_in_the_new_channel() -> None:
    store = FakeStore()
    await update(make_cog(store), make_member(7), 1, 2)
    assert store.calls == [("open", 7, 2, 1000.0)]


async def test_afk_counts_as_leaving_and_joining() -> None:
    store = FakeStore()
    cog = make_cog(store)
    await update(cog, make_member(7), 1, AFK)
    await update(cog, make_member(7), AFK, 2)
    assert store.calls == [("close", 7, 1000.0), ("open", 7, 2, 1000.0)]


async def test_mute_only_changes_nothing() -> None:
    store = FakeStore()
    await update(make_cog(store), make_member(7), 1, 1)
    assert store.calls == []


async def test_bots_are_ignored() -> None:
    store = FakeStore()
    await update(make_cog(store), make_member(7, bot=True), None, 1)
    assert store.calls == []


async def test_other_guilds_are_ignored_when_guild_id_is_set() -> None:
    store = FakeStore()
    await update(make_cog(store), make_member(7, guild=make_guild(guild_id=2)), None, 1)
    assert store.calls == []


async def test_every_guild_is_tracked_without_guild_id() -> None:
    store = FakeStore()
    cog = make_cog(store, guild_id=None)
    await update(cog, make_member(7, guild=make_guild(guild_id=2)), None, 1)
    assert store.calls == [("open", 7, 1, 1000.0)]


async def test_store_errors_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore(fail=True)
    with caplog.at_level(logging.ERROR):
        await update(make_cog(store), make_member(7), None, 1)
    assert "Failed to record a voice state change" in caplog.text


async def test_heartbeat_errors_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore(fail=True)
    with caplog.at_level(logging.ERROR):
        await make_cog(store).heartbeat_loop()
    assert "heartbeat failed" in caplog.text


async def test_heartbeat_uses_the_clock() -> None:
    store = FakeStore()
    await make_cog(store).heartbeat_loop()
    assert store.calls == [("heartbeat", 1000.0)]


def test_members_in_voice_skips_afk_and_bots() -> None:
    guild = make_guild(
        voice={
            1: [make_member(7), make_member(8, bot=True)],
            AFK: [make_member(9)],
            2: [make_member(10)],
        }
    )
    assert members_in_voice([guild]) == [(7, 1), (10, 2)]


async def test_reconcile_closes_stale_then_opens_live_members() -> None:
    store = FakeStore()
    guild = make_guild(voice={1: [make_member(7)]})
    other = make_guild(guild_id=2, voice={3: [make_member(8)]})
    await make_cog(store, guild, other).reconcile()
    assert store.calls == [("close_stale",), ("open", 7, 1, 1000.0)]


async def test_reconcile_errors_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        await make_cog(FakeStore(fail=True), make_guild()).reconcile()
    assert "Failed to reconcile" in caplog.text


async def test_on_ready_reconciles_once(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    cog = make_cog(store, make_guild(voice={1: [make_member(7)]}))
    started: list[bool] = []
    monkeypatch.setattr(cog.heartbeat_loop, "start", lambda: started.append(True))
    await cog.on_ready()
    await cog.on_ready()
    assert store.calls == [("close_stale",), ("open", 7, 1, 1000.0)]
    assert started == [True]


async def test_crash_mid_session_closes_at_last_seen(tmp_path: Path) -> None:
    store = SqliteActivityStore(tmp_path / "activity.sqlite3")
    clock = FakeClock(1000.0)
    guild = make_guild(voice={1: [make_member(7)]})
    first_run = Activity(make_bot(guild), store, GUILD_ID, clock=clock)
    await update(first_run, make_member(7, guild), None, 1)
    clock.now = 1060.0
    await first_run.heartbeat_loop()
    # The process dies here; the bot comes back much later with the member still in voice.
    clock.now = 5000.0
    second_run = Activity(make_bot(guild), store, GUILD_ID, clock=clock)
    await second_run.reconcile()

    sessions = await store.sessions_between(0.0, 10_000.0)
    assert [(s.user_id, s.start_ts, s.end_ts) for s in sessions] == [
        (7, 1000.0, 1060.0),
        (7, 5000.0, None),
    ]


async def test_reconcile_never_leaves_double_sessions(tmp_path: Path) -> None:
    store = SqliteActivityStore(tmp_path / "activity.sqlite3")
    clock = FakeClock(1000.0)
    guild = make_guild(voice={1: [make_member(7)]})
    cog = Activity(make_bot(guild), store, GUILD_ID, clock=clock)
    await update(cog, make_member(7, guild), None, 1)
    clock.now = 1030.0
    await cog.reconcile()
    await update(cog, make_member(7, guild), 1, 2)

    open_sessions: list[Session] = await store.open_sessions()
    assert [(s.user_id, s.channel_id) for s in open_sessions] == [(7, 2)]
