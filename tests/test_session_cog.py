"""/session_poll and the Join/Leave buttons: wiring, cooldown, replies and button ids."""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest
from discord.ext import commands

from ninja_bot.cogs.sessions import (
    POLL_COOLDOWN_SECONDS,
    Sessions,
    cooldown_message,
)
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.sessions import content
from ninja_bot.sessions.service import (
    JOIN_MESSAGES,
    MAX_NAME_LENGTH,
    MAX_OPEN_POLLS,
    JoinResult,
    NoPrefill,
    SessionService,
)
from ninja_bot.sessions.store import STATUS_CANCELLED, STATUS_VOTING, SqliteSessionStore
from ninja_bot.sessions.views import (
    CUSTOM_ID_TEMPLATE,
    PollAction,
    PollButton,
    custom_id,
    poll_view,
)

TZ = ZoneInfo("Europe/Amsterdam")
GUILD = 10
CHANNEL = 100
GM = 1
PLAYER = 2
MESSAGE_ID = 555
SRC = Path(__file__).resolve().parent.parent / "src" / "ninja_bot"


class FakeClock:
    def __init__(self) -> None:
        # Monday 2026-11-02 12:00 UTC.
        self.now = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()

    def __call__(self) -> float:
        return self.now


def make_channel(permissions: discord.Permissions | None = None) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    channel.permissions_for.return_value = (
        discord.Permissions.all() if permissions is None else permissions
    )
    return channel


def make_interaction(user_id: int = GM, channel: Any = None) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.guild.id = GUILD
    interaction.channel = make_channel() if channel is None else channel
    interaction.response.send_message = AsyncMock(return_value=MagicMock(message_id=MESSAGE_ID))
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done.return_value = False
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture
def store(tmp_path: Path) -> SqliteSessionStore:
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def cog(store: SqliteSessionStore, clock: FakeClock) -> Sessions:
    service = SessionService(store, TZ, clock, NoPrefill())
    return Sessions(MagicMock(), service, UserCooldown(POLL_COOLDOWN_SECONDS, clock))


def reply(interaction: MagicMock) -> tuple[Any, dict[str, Any]]:
    """The (content, kwargs) of the one `response.send_message` call."""
    interaction.response.send_message.assert_awaited_once()
    call = interaction.response.send_message.await_args
    return (call.args[0] if call.args else None), call.kwargs


async def start_poll(cog: Sessions, interaction: MagicMock, **kwargs: Any) -> None:
    params: dict[str, Any] = {"name": "Strahd", "days": "fri,sat", "times": "19:00"}
    params.update(kwargs)
    await cog.handle_session_poll(interaction, **params)


# /session_poll


async def test_valid_poll_is_posted_publicly_and_message_id_stored(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    interaction = make_interaction()
    await start_poll(cog, interaction)
    text, kwargs = reply(interaction)
    assert text is None
    assert kwargs.get("ephemeral") is not True
    assert kwargs["embed"].title is not None and "Strahd" in kwargs["embed"].title
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    ids = [item.custom_id for item in kwargs["view"].children]
    polls = await store.open_polls(GUILD)
    assert len(polls) == 1
    poll = polls[0]
    assert ids == [custom_id(poll.id, PollAction.JOIN), custom_id(poll.id, PollAction.LEAVE)]
    assert poll.message_id == MESSAGE_ID
    assert poll.channel_id == CHANNEL
    assert poll.gm_id == GM
    assert await store.players(poll.id) == [GM]


async def test_optional_parameters_reach_the_service(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    interaction = make_interaction()
    await start_poll(
        cog,
        interaction,
        weeks=3,
        start="2026-11-09",
        duration_hours=6,
        min_players=3,
        round_hours=48,
    )
    (poll,) = await store.open_polls(GUILD)
    assert (poll.weeks, poll.duration_hours, poll.min_players, poll.round_hours) == (3, 6, 3, 48)
    assert poll.window_start.isoformat() == "2026-11-09"


async def test_message_id_falls_back_to_the_original_response(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    interaction = make_interaction()
    interaction.response.send_message.return_value = MagicMock(message_id=None)
    interaction.original_response = AsyncMock(return_value=MagicMock(id=777))
    await start_poll(cog, interaction)
    (poll,) = await store.open_polls(GUILD)
    assert poll.message_id == 777


@pytest.mark.parametrize(
    ("kwargs", "message_fragment"),
    [
        ({"name": "   "}, content.BLANK_NAME_MESSAGE),
        ({"name": "x" * (MAX_NAME_LENGTH + 1)}, "at most"),
        ({"days": "someday"}, "weekdays"),
        ({"times": "7pm"}, "24-hour"),
        ({"start": "next week"}, "YYYY-MM-DD"),
        ({"start": "2020-01-01"}, "past"),
        ({"weeks": 9}, "`weeks`"),
        ({"duration_hours": 13}, "`duration_hours`"),
        ({"min_players": 1}, "`min_players`"),
        ({"round_hours": 0}, "`round_hours`"),
        ({"days": "weekend", "times": "10:00,12:00,14:00,16:00", "weeks": 4}, "slots"),
    ],
)
async def test_invalid_input_replies_ephemerally_and_refunds(
    cog: Sessions,
    store: SqliteSessionStore,
    kwargs: dict[str, Any],
    message_fragment: str,
) -> None:
    interaction = make_interaction()
    await start_poll(cog, interaction, **kwargs)
    text, reply_kwargs = reply(interaction)
    assert message_fragment in text
    assert reply_kwargs["ephemeral"] is True
    assert await store.open_polls(GUILD) == []
    assert cog.cooldown.try_acquire(GM) == 0.0


async def test_too_many_open_polls_is_an_ephemeral_input_error(cog: Sessions) -> None:
    for index in range(MAX_OPEN_POLLS):
        await start_poll(cog, make_interaction(user_id=100 + index))
    interaction = make_interaction()
    await start_poll(cog, interaction)
    text, kwargs = reply(interaction)
    assert text == content.TOO_MANY_OPEN_POLLS_MESSAGE.format(max=MAX_OPEN_POLLS)
    assert kwargs["ephemeral"] is True


async def test_cooldown_blocks_a_second_poll(cog: Sessions, clock: FakeClock) -> None:
    await start_poll(cog, make_interaction())
    interaction = make_interaction()
    clock.now += 10
    await start_poll(cog, interaction, name="Second")
    text, kwargs = reply(interaction)
    assert text == cooldown_message(POLL_COOLDOWN_SECONDS - 10)
    assert kwargs["ephemeral"] is True
    clock.now += POLL_COOLDOWN_SECONDS
    later = make_interaction()
    await start_poll(cog, later, name="Third")
    assert reply(later)[0] is None


def test_cooldown_message_rounds_up() -> None:
    assert cooldown_message(0.2) == content.POLL_COOLDOWN_MESSAGE.format(seconds=1, unit="second")
    assert "29 seconds" in cooldown_message(28.1)


async def test_failed_send_refunds_and_cancels_the_poll(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    interaction = make_interaction()
    interaction.response.send_message.side_effect = discord.HTTPException(
        MagicMock(status=500), "boom"
    )
    await start_poll(cog, interaction)
    assert await store.open_polls(GUILD) == []
    assert cog.cooldown.try_acquire(GM) == 0.0


async def test_unexpected_error_is_logged_and_generic(
    cog: Sessions, store: SqliteSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "open_polls", broken)
    interaction = make_interaction()
    await start_poll(cog, interaction)
    text, kwargs = reply(interaction)
    assert text == content.GENERIC_ERROR_MESSAGE
    assert kwargs["ephemeral"] is True
    assert cog.cooldown.try_acquire(GM) == 0.0


async def test_channel_without_permissions_is_refused(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    channel = make_channel(discord.Permissions(view_channel=True, send_messages=True))
    interaction = make_interaction(channel=channel)
    await start_poll(cog, interaction)
    text, kwargs = reply(interaction)
    assert text == content.CHANNEL_PROBLEM_MESSAGE
    assert kwargs["ephemeral"] is True
    assert await store.open_polls(GUILD) == []
    assert cog.cooldown.try_acquire(GM) == 0.0


async def test_non_text_channel_is_refused(cog: Sessions) -> None:
    interaction = make_interaction(channel=MagicMock(spec=discord.CategoryChannel))
    await start_poll(cog, interaction)
    assert reply(interaction)[0] == content.CHANNEL_PROBLEM_MESSAGE


async def test_without_guild_replies_guild_only(cog: Sessions) -> None:
    interaction = make_interaction()
    interaction.guild = None
    await start_poll(cog, interaction)
    assert reply(interaction)[0] == content.GUILD_ONLY_MESSAGE


def test_command_is_guild_only_with_described_parameters() -> None:
    command = Sessions.session_poll
    assert command.guild_only
    params = {param.name: param for param in command.parameters}
    assert list(params) == [
        "name",
        "days",
        "times",
        "weeks",
        "start",
        "duration_hours",
        "min_players",
        "round_hours",
    ]
    assert all(str(param.description).strip() for param in params.values())
    assert command._params["name"].to_dict()["max_length"] == MAX_NAME_LENGTH
    assert (params["weeks"].min_value, params["weeks"].max_value) == (1, 8)
    assert params["weeks"].default == 2
    assert params["duration_hours"].default == 4
    assert (params["min_players"].min_value, params["min_players"].max_value) == (2, 25)
    assert (params["round_hours"].min_value, params["round_hours"].max_value) == (1, 336)
    assert not params["min_players"].required
    assert not params["start"].required


# Join / Leave buttons


async def make_poll(cog: Sessions, store: SqliteSessionStore) -> int:
    await start_poll(cog, make_interaction())
    (poll,) = await store.open_polls(GUILD)
    return poll.id


async def test_join_re_renders_the_poll_and_confirms_privately(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    interaction = make_interaction(user_id=PLAYER)
    await cog.handle_poll_action(interaction, poll_id, PollAction.JOIN, None)
    assert await store.players(poll_id) == [GM, PLAYER]
    edit = interaction.response.edit_message.await_args.kwargs
    joined = next(field for field in edit["embed"].fields if field.name == content.JOINED_FIELD)
    assert f"<@{PLAYER}>" in str(joined.value)
    assert [item.custom_id for item in edit["view"].children][0] == custom_id(
        poll_id, PollAction.JOIN
    )
    interaction.followup.send.assert_awaited_once_with(
        JOIN_MESSAGES[JoinResult.JOINED], ephemeral=True
    )


async def test_leave_re_renders_the_poll(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    interaction = make_interaction(user_id=PLAYER)
    await cog.handle_poll_action(interaction, poll_id, PollAction.LEAVE, None)
    assert await store.players(poll_id) == [GM]
    interaction.response.edit_message.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with(
        JOIN_MESSAGES[JoinResult.LEFT], ephemeral=True
    )


@pytest.mark.parametrize(
    ("user_id", "action", "result"),
    [
        (GM, PollAction.JOIN, JoinResult.ALREADY_JOINED),
        (GM, PollAction.LEAVE, JoinResult.GM_CANNOT_LEAVE),
        (PLAYER, PollAction.LEAVE, JoinResult.NOT_JOINED),
    ],
)
async def test_refusals_are_ephemeral_and_leave_the_message(
    cog: Sessions,
    store: SqliteSessionStore,
    user_id: int,
    action: PollAction,
    result: JoinResult,
) -> None:
    poll_id = await make_poll(cog, store)
    interaction = make_interaction(user_id=user_id)
    await cog.handle_poll_action(interaction, poll_id, action, None)
    assert reply(interaction) == (JOIN_MESSAGES[result], {"ephemeral": True})
    interaction.response.edit_message.assert_not_awaited()


async def test_closed_or_unknown_poll_refuses_join(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    await store.cancel_poll(poll_id)
    for target in (poll_id, 9999):
        interaction = make_interaction(user_id=PLAYER)
        await cog.handle_join(interaction, target)
        assert reply(interaction)[0] == JOIN_MESSAGES[JoinResult.POLL_CLOSED]
    assert (await store.get_poll(poll_id)).status == STATUS_CANCELLED  # type: ignore[union-attr]


async def test_unknown_action_is_a_stale_button(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id = await make_poll(cog, store)
    interaction = make_interaction(user_id=PLAYER)
    await cog.handle_poll_action(interaction, poll_id, "explode", None)
    assert reply(interaction) == (content.STALE_BUTTON_MESSAGE, {"ephemeral": True})


async def test_button_errors_reply_generic(
    cog: Sessions, store: SqliteSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    poll_id = await make_poll(cog, store)

    async def broken(*args: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "players", broken)
    interaction = make_interaction(user_id=PLAYER)
    await cog.handle_poll_action(interaction, poll_id, PollAction.JOIN, None)
    assert reply(interaction) == (content.GENERIC_ERROR_MESSAGE, {"ephemeral": True})
    assert (await store.get_poll(poll_id)).status == STATUS_VOTING  # type: ignore[union-attr]


# Dynamic button ids


@pytest.mark.parametrize(
    ("poll_id", "action", "arg"),
    [(1, "join", None), (123456789012, "leave", None), (7, "confirm", 42)],
)
async def test_custom_id_round_trip(poll_id: int, action: str, arg: int | None) -> None:
    text = custom_id(poll_id, action, arg)
    match = re.fullmatch(CUSTOM_ID_TEMPLATE, text)
    assert match is not None
    button = await PollButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert (button.poll_id, button.action, button.arg) == (poll_id, action, arg)
    assert button.custom_id == text


@pytest.mark.parametrize("text", ["ses:x:join", "ses:1:JOIN", "other:1:join", "ses:1:join:x"])
def test_template_rejects_foreign_ids(text: str) -> None:
    assert re.fullmatch(CUSTOM_ID_TEMPLATE, text) is None


def test_poll_view_is_persistent_with_join_and_leave() -> None:
    view = poll_view(5)
    assert view.timeout is None
    assert view.is_persistent()
    labels = [item.item.label for item in view.children if isinstance(item, PollButton)]
    assert labels == [content.JOIN_BUTTON, content.LEAVE_BUTTON]


async def test_button_callback_hands_the_click_to_the_cog() -> None:
    handler = MagicMock(spec=Sessions)
    handler.handle_poll_action = AsyncMock()
    bot = MagicMock(spec=commands.Bot)
    bot.get_cog.return_value = handler
    interaction = MagicMock()
    interaction.client = bot
    button = PollButton(5, PollAction.LEAVE)
    await button.callback(interaction)
    bot.get_cog.assert_called_once_with("Sessions")
    handler.handle_poll_action.assert_awaited_once_with(interaction, 5, "leave", None)


async def test_button_callback_without_the_cog_is_stale() -> None:
    bot = MagicMock(spec=commands.Bot)
    bot.get_cog.return_value = None
    interaction = MagicMock()
    interaction.client = bot
    interaction.response.send_message = AsyncMock()
    await PollButton(5, PollAction.JOIN).callback(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        content.STALE_BUTTON_MESSAGE, ephemeral=True
    )


async def test_cog_registers_the_dynamic_button(cog: Sessions) -> None:
    await cog.cog_load()
    cog.bot.add_dynamic_items.assert_called_once_with(PollButton)  # type: ignore[attr-defined]


def test_only_views_imports_discord_ui() -> None:
    importers = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [f"{module}.{alias.name}" for alias in node.names]
            if any(name == "discord.ui" or name.startswith("discord.ui.") for name in names):
                importers.append(path.relative_to(SRC).as_posix())
    assert importers == ["sessions/views.py"]
