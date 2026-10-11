"""/sessions and /session_cancel: list, autocomplete, permissions, cancel and its effects."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from ninja_bot.cogs.sessions import (
    LIST_COOLDOWN_SECONDS,
    POLL_COOLDOWN_SECONDS,
    Sessions,
    cooldown_message,
)
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.sessions import cancel, content
from ninja_bot.sessions.cancel import (
    CHOICE_NAME_LIMIT,
    MAX_CHOICES,
    CancelItem,
    cancel_choices,
    parse_cancel_item,
)
from ninja_bot.sessions.service import NoPrefill, RoundFits, SessionService
from ninja_bot.sessions.store import (
    STATUS_CANCELLED,
    Poll,
    Session,
    SqliteSessionStore,
)

TZ = ZoneInfo("Europe/Amsterdam")
GUILD = 10
OTHER_GUILD = 11
CHANNEL = 100
GM = 1
PLAYER = 2
POLL_MESSAGE = 555
HOUR = 3600


class FakeClock:
    def __init__(self) -> None:
        # Monday 2026-11-02 12:00 UTC.
        self.now = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path: Path) -> SqliteSessionStore:
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def cog(store: SqliteSessionStore, clock: FakeClock) -> Sessions:
    service = SessionService(store, TZ, clock, NoPrefill())
    bot = MagicMock()
    bot.get_partial_messageable.return_value.get_partial_message.return_value.edit = AsyncMock()
    return Sessions(
        bot,
        service,
        UserCooldown(POLL_COOLDOWN_SECONDS, clock),
        list_cooldown=UserCooldown(LIST_COOLDOWN_SECONDS, clock),
    )


def make_interaction(user_id: int = GM, guild_id: int | None = GUILD) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    if guild_id is None:
        interaction.guild = None
    else:
        interaction.guild.id = guild_id
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done.return_value = False
    interaction.permissions = discord.Permissions.none()
    interaction.followup.send = AsyncMock()
    return interaction


def reply(interaction: MagicMock) -> tuple[Any, dict[str, Any]]:
    interaction.response.send_message.assert_awaited_once()
    call = interaction.response.send_message.await_args
    return (call.args[0] if call.args else None), call.kwargs


def message_edit(cog: Sessions) -> AsyncMock:
    bot: Any = cog.bot
    edit: AsyncMock = bot.get_partial_messageable.return_value.get_partial_message.return_value.edit
    return edit


async def open_poll(cog: Sessions, name: str = "Strahd", guild_id: int = GUILD) -> Poll:
    poll = await cog.service.create_poll(guild_id, CHANNEL, GM, name, "fri,sat", "19:00")
    await cog.service.store.set_poll_message(poll.id, POLL_MESSAGE)
    await cog.service.join(poll.id, PLAYER)
    reloaded = await cog.service.store.get_poll(poll.id)
    assert reloaded is not None
    return reloaded


async def confirmed_session(cog: Sessions, name: str = "One-shot") -> Session:
    poll = await open_poll(cog, name)
    slots = [slot.id for slot in await cog.service.store.poll_slots(poll.id, 1)]
    await cog.service.submit_vote(poll.id, GM, slots, [])
    await cog.service.submit_vote(poll.id, PLAYER, slots, [])
    assert isinstance(await cog.service.close_round(poll.id, GM, False, "manual"), RoundFits)
    return await cog.service.confirm(poll.id, slots[0], GM, False)


# /sessions


async def test_list_shows_polls_pending_and_sessions_publicly(cog: Sessions) -> None:
    session = await confirmed_session(cog)
    poll = await open_poll(cog)
    interaction = make_interaction(user_id=PLAYER)
    await cog.handle_sessions(interaction)
    text, kwargs = reply(interaction)
    assert text is None
    assert kwargs.get("ephemeral") is not True
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    fields = {field.name: field.value or "" for field in kwargs["embed"].fields}
    polls = fields[content.LIST_POLLS_FIELD]
    assert f"/{GUILD}/{CHANNEL}/{POLL_MESSAGE}" in polls
    assert "Strahd" in polls and f"<@{GM}>" in polls and f"<@{PLAYER}>" in polls
    sessions = fields[content.LIST_SESSIONS_FIELD]
    assert f"<t:{int(session.start)}:F>" in sessions and "One-shot" in sessions
    assert poll.id != session.poll_id


async def test_empty_list_has_the_empty_state(cog: Sessions) -> None:
    await open_poll(cog, guild_id=OTHER_GUILD)
    interaction = make_interaction()
    await cog.handle_sessions(interaction)
    _, kwargs = reply(interaction)
    assert kwargs["embed"].description == content.LIST_EMPTY_MESSAGE


async def test_list_cooldown_blocks_then_expires(cog: Sessions, clock: FakeClock) -> None:
    await cog.handle_sessions(make_interaction())
    blocked = make_interaction()
    await cog.handle_sessions(blocked)
    text, kwargs = reply(blocked)
    assert text == cooldown_message(LIST_COOLDOWN_SECONDS, content.LIST_COOLDOWN_MESSAGE)
    assert kwargs["ephemeral"] is True
    clock.now += LIST_COOLDOWN_SECONDS
    later = make_interaction()
    await cog.handle_sessions(later)
    assert reply(later)[1].get("embed") is not None


async def test_list_failure_refunds_the_cooldown(cog: Sessions) -> None:
    failing = make_interaction()
    failing.response.send_message.side_effect = discord.HTTPException(MagicMock(), "nope")
    await cog.handle_sessions(failing)
    again = make_interaction()
    await cog.handle_sessions(again)
    assert reply(again)[1].get("embed") is not None


async def test_list_store_error_replies_generic_and_refunds(
    cog: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cog.service, "overview", AsyncMock(side_effect=RuntimeError("db")))
    interaction = make_interaction()
    await cog.handle_sessions(interaction)
    assert reply(interaction) == (content.GENERIC_ERROR_MESSAGE, {"ephemeral": True})
    assert cog.list_cooldown.try_acquire(GM) == 0


async def test_list_without_guild_is_guild_only(cog: Sessions) -> None:
    interaction = make_interaction(guild_id=None)
    await cog.handle_sessions(interaction)
    assert reply(interaction) == (content.GUILD_ONLY_MESSAGE, {"ephemeral": True})


# Autocomplete


async def test_autocomplete_lists_this_guilds_polls_and_sessions(cog: Sessions) -> None:
    session = await confirmed_session(cog)
    poll = await open_poll(cog)
    await open_poll(cog, "Elsewhere", guild_id=OTHER_GUILD)
    choices = await cog.cancel_choices(make_interaction(), "")
    assert choices == [
        (
            content.CANCEL_POLL_CHOICE.format(name="Strahd", date="Tue 3 Nov"),
            f"poll:{poll.id}",
        ),
        (
            content.CANCEL_SESSION_CHOICE.format(name="One-shot", start="Fri 6 Nov 19:00"),
            f"session:{session.id}",
        ),
    ]
    assert await cog.cancel_choices(make_interaction(), "strahd") == choices[:1]
    assert await cog.cancel_choices(make_interaction(), "  SESSION ") == choices[1:]
    assert await cog.cancel_choices(make_interaction(guild_id=None), "") == []


async def test_autocomplete_choice_objects(cog: Sessions) -> None:
    poll = await open_poll(cog)
    choices = await cog._session_cancel_item(make_interaction(), "")  # type: ignore[call-arg]
    assert [(choice.name, choice.value) for choice in choices] == [
        (content.CANCEL_POLL_CHOICE.format(name="Strahd", date="Tue 3 Nov"), f"poll:{poll.id}")
    ]


async def test_autocomplete_errors_give_no_choices(
    cog: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cog.service.store, "open_polls", AsyncMock(side_effect=RuntimeError()))
    assert await cog.cancel_choices(make_interaction(), "") == []


def make_poll(poll_id: int, name: str) -> Poll:
    return Poll(
        id=poll_id,
        guild_id=GUILD,
        channel_id=CHANNEL,
        message_id=None,
        name=name,
        gm_id=GM,
        days=(4,),
        times=(),
        weeks=2,
        duration_hours=4,
        min_players=0,
        round_hours=0,
        round_number=1,
        window_start=date(2026, 11, 3),
        status="voting",
        created_at=0,
        round_started_at=0,
    )


def test_choices_are_capped_and_labels_cut() -> None:
    polls = [make_poll(i, f"Poll {i}") for i in range(30)]
    choices = cancel_choices(polls, [], TZ)
    assert len(choices) == MAX_CHOICES
    assert choices[0] == ("Poll: Poll 0 (from Tue 3 Nov)", "poll:0")
    long = cancel_choices([make_poll(1, "x" * 200)], [], TZ)
    assert len(long[0][0]) == CHOICE_NAME_LIMIT
    filtered = cancel_choices(polls, [], TZ, "poll 2")
    assert [value for _, value in filtered] == [f"poll:{i}" for i in (2, *range(20, 30))]
    assert len(cancel_choices(polls, [], TZ, "poll", limit=3)) == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("poll:3", CancelItem("poll", 3)),
        ("session:12", CancelItem("session", 12)),
        (" poll:3 ", CancelItem("poll", 3)),
        ("poll:", None),
        ("poll:-1", None),
        ("poll:x", None),
        ("poll:٣", None),
        ("event:3", None),
        ("Strahd", None),
        ("", None),
    ],
)
def test_parse_cancel_item(value: str, expected: CancelItem | None) -> None:
    assert parse_cancel_item(value) == expected
    if expected is not None:
        assert parse_cancel_item(expected.value) == expected


def test_cancel_module_does_not_import_discord() -> None:
    tree = ast.parse(Path(cancel.__file__).read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not any(name.split(".")[0] == "discord" for name in modules)


# /session_cancel


async def test_gm_cancels_a_poll(cog: Sessions, store: SqliteSessionStore) -> None:
    poll = await open_poll(cog)
    interaction = make_interaction()
    await cog.handle_session_cancel(interaction, f"poll:{poll.id}")
    text, kwargs = reply(interaction)
    assert text == content.POLL_CANCELLED_MESSAGE.format(name="Strahd", user=f"<@{GM}>")
    assert kwargs.get("ephemeral") is not True
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    assert (await store.get_poll(poll.id)).status == STATUS_CANCELLED  # type: ignore[union-attr]
    edit = message_edit(cog).await_args.kwargs
    assert edit["content"] == content.CANCELLED_POLL_EDIT
    assert edit["view"] is None
    bot: Any = cog.bot
    bot.get_partial_messageable.assert_called_with(CHANNEL)
    bot.get_partial_messageable.return_value.get_partial_message.assert_called_with(POLL_MESSAGE)
    # Gone from the list, the autocomplete and the nudges.
    assert await cog.cancel_choices(make_interaction(), "") == []
    assert (await cog.service.overview(GUILD)).polls == []
    assert await cog.notifier.due_nudges() == []


async def test_manager_cancels_a_session(cog: Sessions, clock: FakeClock) -> None:
    session = await confirmed_session(cog)
    interaction = make_interaction(user_id=PLAYER)
    interaction.permissions = discord.Permissions(manage_events=True)
    await cog.handle_session_cancel(interaction, f"session:{session.id}")
    text, kwargs = reply(interaction)
    assert text == content.SESSION_CANCELLED_MESSAGE.format(
        name="One-shot", start=f"<t:{int(session.start)}:F>", user=f"<@{PLAYER}>"
    )
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    assert message_edit(cog).await_args.kwargs["content"] == content.CANCELLED_SESSION_EDIT
    stored = await cog.service.store.get_session(session.id)
    assert stored is not None and stored.cancelled
    assert (await cog.service.overview(GUILD)).sessions == []
    clock.now = session.start - HOUR
    assert await cog.notifier.due_reminders() == []


async def test_player_cannot_cancel(cog: Sessions, store: SqliteSessionStore) -> None:
    poll = await open_poll(cog)
    session = await confirmed_session(cog)
    for item in (f"poll:{poll.id}", f"session:{session.id}"):
        interaction = make_interaction(user_id=PLAYER)
        await cog.handle_session_cancel(interaction, item)
        assert reply(interaction) == (content.NOT_ALLOWED_MESSAGE, {"ephemeral": True})
    assert (await store.get_poll(poll.id)).status != STATUS_CANCELLED  # type: ignore[union-attr]
    stored = await store.get_session(session.id)
    assert stored is not None and not stored.cancelled
    message_edit(cog).assert_not_awaited()


@pytest.mark.parametrize("item", ["poll:999", "session:999", "Strahd", "poll:abc"])
async def test_unknown_items_are_ephemeral(cog: Sessions, item: str) -> None:
    interaction = make_interaction()
    await cog.handle_session_cancel(interaction, item)
    _, kwargs = reply(interaction)
    assert kwargs == {"ephemeral": True}
    message_edit(cog).assert_not_awaited()


async def test_finished_items_and_other_guilds_are_refused(cog: Sessions, clock: FakeClock) -> None:
    session = await confirmed_session(cog)
    elsewhere = await open_poll(cog, "Elsewhere", guild_id=OTHER_GUILD)
    first = make_interaction()
    await cog.handle_session_cancel(first, f"poll:{session.poll_id}")
    assert reply(first) == (content.UNKNOWN_POLL_MESSAGE, {"ephemeral": True})
    other = make_interaction()
    await cog.handle_session_cancel(other, f"poll:{elsewhere.id}")
    assert reply(other) == (content.UNKNOWN_POLL_MESSAGE, {"ephemeral": True})
    clock.now = session.start
    started = make_interaction()
    await cog.handle_session_cancel(started, f"session:{session.id}")
    assert reply(started) == (content.UNKNOWN_SESSION_MESSAGE, {"ephemeral": True})


async def test_cancel_twice_is_refused_the_second_time(cog: Sessions) -> None:
    poll = await open_poll(cog)
    await cog.handle_session_cancel(make_interaction(), f"poll:{poll.id}")
    again = make_interaction()
    await cog.handle_session_cancel(again, f"poll:{poll.id}")
    assert reply(again) == (content.UNKNOWN_POLL_MESSAGE, {"ephemeral": True})


async def test_cancel_without_message_id_skips_the_edit(cog: Sessions) -> None:
    poll = await cog.service.create_poll(GUILD, CHANNEL, GM, "Strahd", "fri", "19:00")
    interaction = make_interaction()
    await cog.handle_session_cancel(interaction, f"poll:{poll.id}")
    assert reply(interaction)[0] == content.POLL_CANCELLED_MESSAGE.format(
        name="Strahd", user=f"<@{GM}>"
    )
    message_edit(cog).assert_not_awaited()


async def test_failed_edit_still_cancels(cog: Sessions, store: SqliteSessionStore) -> None:
    poll = await open_poll(cog)
    message_edit(cog).side_effect = discord.NotFound(MagicMock(), "gone")
    interaction = make_interaction()
    await cog.handle_session_cancel(interaction, f"poll:{poll.id}")
    assert reply(interaction)[0] is not None
    assert (await store.get_poll(poll.id)).status == STATUS_CANCELLED  # type: ignore[union-attr]


def test_commands_are_guild_only_with_descriptions() -> None:
    for command in (Sessions.sessions, Sessions.session_cancel):
        assert command.guild_only is True
        assert command.description
        for parameter in command.parameters:
            assert parameter.description and parameter.description != "…"
    (item,) = Sessions.session_cancel.parameters
    assert item.name == "item" and item.required and item.autocomplete
