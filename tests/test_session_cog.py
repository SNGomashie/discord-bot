"""/session_poll and the Join/Leave buttons: wiring, cooldown, replies and button ids."""

from __future__ import annotations

import ast
import asyncio
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
from ninja_bot.sessions import content, slots
from ninja_bot.sessions.players import INVALID_PLAYERS_MESSAGE
from ninja_bot.sessions.scoring import MAX_ROUNDS, Vote
from ninja_bot.sessions.service import (
    JOIN_MESSAGES,
    MAX_NAME_LENGTH,
    MAX_OPEN_POLLS,
    VOTE_MESSAGES,
    JoinResult,
    NoPrefill,
    NotReady,
    RoundAdvanced,
    RoundFailed,
    RoundFits,
    SessionService,
    VoteResult,
)
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_VOTING,
    SqliteSessionStore,
)
from ninja_bot.sessions.views import (
    CUSTOM_ID_TEMPLATE,
    PollAction,
    PollButton,
    VoteView,
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
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = False
    interaction.permissions = discord.Permissions.none()
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
    assert ids == [
        custom_id(poll.id, PollAction.JOIN),
        custom_id(poll.id, PollAction.LEAVE),
        custom_id(poll.id, PollAction.VOTE),
        custom_id(poll.id, PollAction.CLOSE),
    ]
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
        "exclude",
        "players",
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
    assert not params["exclude"].required
    assert not params["players"].required


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


# Vote view


def poll_message_edit(cog: Sessions) -> AsyncMock:
    """The `edit` of the public poll message, reached via `get_partial_messageable`."""
    edit = AsyncMock()
    bot: Any = cog.bot
    bot.get_partial_messageable.return_value.get_partial_message.return_value.edit = edit
    return edit


async def open_vote(cog: Sessions, poll_id: int, user_id: int) -> VoteView:
    interaction = make_interaction(user_id=user_id)
    await cog.handle_poll_action(interaction, poll_id, PollAction.VOTE, None)
    text, kwargs = reply(interaction)
    assert kwargs["ephemeral"] is True
    assert text == content.VOTE_PROMPT.format(tz=TZ.key)
    view = kwargs["view"]
    assert isinstance(view, VoteView)
    return view


async def save(view: VoteView, user_id: int, *, none: bool = False) -> MagicMock:
    interaction = make_interaction(user_id=user_id)
    button = view.none_of_these if none else view.save
    await button.callback(interaction)
    return interaction


async def slot_ids(store: SqliteSessionStore, poll_id: int) -> list[int]:
    return [slot.id for slot in await store.poll_slots(poll_id, 1)]


def default_ids(select: Any) -> list[int]:
    return [int(option.value) for option in select.options if option.default]


async def test_vote_view_lists_slots_and_preselects_stored_votes(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    first, second, *rest = await slot_ids(store, poll_id)
    await cog.service.submit_vote(poll_id, GM, [first], [second])
    view = await open_vote(cog, poll_id, GM)
    assert view.timeout == 600
    assert default_ids(view.yes_select) == [first]
    assert default_ids(view.maybe_select) == [second]
    assert view.yes_select.min_values == 0
    assert view.yes_select.max_values == len(rest) + 2
    # Friday 2026-11-06 19:00 in Amsterdam is the first slot.
    assert view.yes_select.options[0].label == "Fri 6 Nov 19:00"
    assert (view.yes_ids, view.maybe_ids) == ([first], [second])


async def test_select_callback_keeps_the_choice_and_defers(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    first, second, *_ = await slot_ids(store, poll_id)
    view = await open_vote(cog, poll_id, GM)
    view.yes_select._values = [str(second), str(first)]
    interaction = make_interaction()
    interaction.response.defer = AsyncMock()
    await view.yes_select.callback(interaction)
    interaction.response.defer.assert_awaited_once_with()
    assert view.yes_ids == [first, second]


async def test_save_records_votes_refreshes_and_confirms_privately(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    first, second, *_ = await slot_ids(store, poll_id)
    edit = poll_message_edit(cog)
    view = await open_vote(cog, poll_id, GM)
    # A slot picked in both lists counts as YES.
    view.pick("yes", [first])
    view.pick("maybe", [first, second])
    interaction = await save(view, GM)
    interaction.response.edit_message.assert_awaited_once_with(
        content=VOTE_MESSAGES[VoteResult.RECORDED], view=None
    )
    votes = await store.votes(poll_id, 1)
    assert votes[(GM, first)] is Vote.YES
    assert votes[(GM, second)] is Vote.IF_NEEDED
    assert {vote for (user, _), vote in votes.items() if user == GM} >= {Vote.NO}
    assert view.is_finished()
    cog.bot.get_partial_messageable.assert_called_with(CHANNEL)  # type: ignore[attr-defined]
    edit.assert_awaited_once()
    kwargs = edit.await_args.kwargs
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    lines = str(kwargs["embed"].description)
    assert "✅ 1 · 🤔 0" in lines and "✅ 0 · 🤔 1" in lines


async def test_none_of_these_records_all_no(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    poll_message_edit(cog)
    await cog.service.submit_vote(poll_id, PLAYER, await slot_ids(store, poll_id), [])
    view = await open_vote(cog, poll_id, PLAYER)
    await save(view, PLAYER, none=True)
    votes = await store.votes(poll_id, 1)
    mine = [vote for (user, _), vote in votes.items() if user == PLAYER]
    assert mine and set(mine) == {Vote.NO}


async def test_empty_save_is_refused_and_the_form_stays_open(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    edit = poll_message_edit(cog)
    view = await open_vote(cog, poll_id, GM)
    interaction = await save(view, GM)
    assert reply(interaction) == (content.VOTE_EMPTY_MESSAGE, {"ephemeral": True})
    interaction.response.edit_message.assert_not_awaited()
    assert not view.is_finished()
    assert await store.votes(poll_id, 1) == {}
    edit.assert_not_awaited()
    # Clearing earlier picks is refused the same way; picking a slot afterwards still saves.
    first, *_ = await slot_ids(store, poll_id)
    view.pick("yes", [first])
    view.pick("yes", [])
    interaction = await save(view, GM)
    assert reply(interaction) == (content.VOTE_EMPTY_MESSAGE, {"ephemeral": True})
    view.pick("maybe", [first])
    interaction = await save(view, GM)
    interaction.response.edit_message.assert_awaited_once_with(
        content=VOTE_MESSAGES[VoteResult.RECORDED], view=None
    )
    assert (await store.votes(poll_id, 1))[(GM, first)] is Vote.IF_NEEDED
    assert view.is_finished()


async def test_prefilled_save_without_changes_keeps_the_votes(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    poll_message_edit(cog)
    first, second, *_ = await slot_ids(store, poll_id)
    await cog.service.submit_vote(poll_id, GM, [first], [second])
    view = await open_vote(cog, poll_id, GM)
    interaction = await save(view, GM)
    interaction.response.edit_message.assert_awaited_once_with(
        content=VOTE_MESSAGES[VoteResult.RECORDED], view=None
    )
    votes = await store.votes(poll_id, 1)
    assert (votes[(GM, first)], votes[(GM, second)]) == (Vote.YES, Vote.IF_NEEDED)


async def test_vote_refused_for_non_members_and_closed_polls(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    interaction = make_interaction(user_id=PLAYER)
    await cog.handle_poll_action(interaction, poll_id, PollAction.VOTE, None)
    assert reply(interaction) == (content.NOT_JOINED_MESSAGE, {"ephemeral": True})
    await store.cancel_poll(poll_id)
    for target in (poll_id, 9999):
        interaction = make_interaction(user_id=GM)
        await cog.handle_vote(interaction, target)
        assert reply(interaction) == (content.POLL_CLOSED_MESSAGE, {"ephemeral": True})


async def test_save_after_the_poll_closed_is_refused_without_refresh(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    edit = poll_message_edit(cog)
    view = await open_vote(cog, poll_id, GM)
    view.pick("yes", (await slot_ids(store, poll_id))[:1])
    await store.cancel_poll(poll_id)
    interaction = await save(view, GM)
    interaction.response.edit_message.assert_awaited_once_with(
        content=VOTE_MESSAGES[VoteResult.POLL_CLOSED], view=None
    )
    edit.assert_not_awaited()


async def test_failed_refresh_does_not_break_the_vote(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    edit = poll_message_edit(cog)
    edit.side_effect = discord.HTTPException(MagicMock(status=404), "Unknown Message")
    view = await open_vote(cog, poll_id, GM)
    interaction = await save(view, GM, none=True)
    interaction.response.edit_message.assert_awaited_once()
    assert await store.votes(poll_id, 1)


async def test_refresh_edits_only_the_embed_of_a_voting_poll(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    edit = poll_message_edit(cog)
    await cog.refresh_poll_message(poll_id)
    edit.assert_awaited_once()
    kwargs = edit.await_args.kwargs
    assert set(kwargs) == {"embed", "allowed_mentions"}
    assert isinstance(kwargs["embed"], discord.Embed)


@pytest.mark.parametrize(
    "status", [STATUS_AWAITING_CONFIRM, STATUS_CONFIRMED, STATUS_FAILED, STATUS_CANCELLED]
)
async def test_refresh_leaves_closed_polls_alone(
    cog: Sessions, store: SqliteSessionStore, status: str
) -> None:
    poll_id = await make_poll(cog, store)
    edit = poll_message_edit(cog)
    await store.set_status(poll_id, status)
    await cog.refresh_poll_message(poll_id)
    edit.assert_not_awaited()


async def test_refresh_racing_a_close_keeps_the_buttons_removed(
    cog: Sessions, store: SqliteSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    poll_id = await make_poll(cog, store)
    _, edit = channel_mocks(cog)
    views: list[Any] = ["buttons"]

    async def record(**kwargs: Any) -> None:
        if "view" in kwargs:
            views.append(kwargs["view"])

    edit.side_effect = record
    render = cog.poll_embed

    async def close_meanwhile(poll: Any) -> discord.Embed:
        # The refresh read a voting poll; the round closes before its edit lands.
        monkeypatch.setattr(cog, "poll_embed", render)
        embed = await render(poll)
        await cog.close_and_post(poll_id, "manual", GM)
        return embed

    monkeypatch.setattr(cog, "poll_embed", close_meanwhile)
    await cog.refresh_poll_message(poll_id)
    assert edit.await_count == 2
    assert "view" not in edit.await_args.kwargs
    assert views[-1] is None


async def test_unexpected_save_error_replies_generic(
    cog: Sessions, store: SqliteSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    poll_id = await make_poll(cog, store)

    async def broken(*args: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "record_votes", broken)
    interaction = make_interaction()
    await cog.handle_vote_save(interaction, poll_id, [], [])
    assert reply(interaction) == (content.GENERIC_ERROR_MESSAGE, {"ephemeral": True})


async def test_vote_checks_auto_close_after_saving(
    cog: Sessions, store: SqliteSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    poll_message_edit(cog)
    checked = AsyncMock()
    monkeypatch.setattr(cog, "advance_if_ready", checked)
    await cog.handle_vote_save(make_interaction(), poll_id, [], [])
    checked.assert_awaited_once_with(poll_id)


async def test_advance_if_ready_closes_once_everyone_voted(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    send, _ = channel_mocks(cog)
    first = (await slot_ids(store, poll_id))[0]
    await cog.service.submit_vote(poll_id, GM, [first], [])
    assert await cog.advance_if_ready(poll_id) is None
    send.assert_not_awaited()
    await cog.service.submit_vote(poll_id, PLAYER, [], [first])
    assert isinstance(await cog.advance_if_ready(poll_id), RoundFits)
    send.assert_awaited_once()
    assert (await store.get_poll(poll_id)).status == STATUS_AWAITING_CONFIRM  # type: ignore[union-attr]
    assert await cog.advance_if_ready(poll_id) is None
    assert await cog.advance_if_ready(9999) is None
    send.assert_awaited_once()


async def test_auto_close_that_is_no_longer_ready_posts_nothing(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    send, edit = channel_mocks(cog)
    for reason in ("all_voted", "deadline"):
        outcome = await cog.close_and_post(poll_id, reason)  # type: ignore[arg-type]
        assert isinstance(outcome, NotReady)
    send.assert_not_awaited()
    edit.assert_not_awaited()
    assert (await store.get_poll(poll_id)).status == STATUS_VOTING  # type: ignore[union-attr]


async def test_advance_if_ready_errors_give_none(
    cog: Sessions, store: SqliteSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    poll_id = await make_poll(cog, store)

    async def broken(*args: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "votes", broken)
    assert await cog.advance_if_ready(poll_id) is None


# Close round and Confirm

NEW_MESSAGE_ID = 777


def channel_mocks(cog: Sessions) -> tuple[AsyncMock, AsyncMock]:
    """(`send` to the poll channel, `edit` of the old poll message)."""
    bot: Any = cog.bot
    channel = bot.get_partial_messageable.return_value
    channel.send = AsyncMock(return_value=MagicMock(id=NEW_MESSAGE_ID))
    channel.get_partial_message.return_value.edit = AsyncMock()
    return channel.send, channel.get_partial_message.return_value.edit


def pinged(kwargs: dict[str, Any]) -> list[int]:
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert mentions.roles is False
    users = mentions.users
    return [] if users is False else [user.id for user in users]


async def two_player_poll(cog: Sessions, store: SqliteSessionStore) -> tuple[int, list[int]]:
    poll_id = await make_poll(cog, store)
    await cog.handle_join(make_interaction(user_id=PLAYER), poll_id)
    return poll_id, await slot_ids(store, poll_id)


async def press_close(cog: Sessions, poll_id: int, user_id: int = GM) -> MagicMock:
    interaction = make_interaction(user_id=user_id)
    await cog.handle_poll_action(interaction, poll_id, PollAction.CLOSE, None)
    return interaction


async def press_confirm(
    cog: Sessions, poll_id: int, slot_id: int | None, user_id: int = GM
) -> MagicMock:
    interaction = make_interaction(user_id=user_id)
    await cog.handle_poll_action(interaction, poll_id, PollAction.CONFIRM, slot_id)
    return interaction


def assert_closed_edit(edit: AsyncMock, round_number: int) -> None:
    kwargs = edit.await_args.kwargs
    assert kwargs["content"] == content.ROUND_CLOSED_MESSAGE.format(round=round_number)
    assert kwargs["view"] is None
    assert pinged(kwargs) == []


async def test_close_fits_posts_the_result_with_confirm_buttons(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id, slots = await two_player_poll(cog, store)
    send, edit = channel_mocks(cog)
    await cog.service.submit_vote(poll_id, GM, [slots[1], slots[0]], [])
    await cog.service.submit_vote(poll_id, PLAYER, [slots[1]], [slots[0]])
    interaction = await press_close(cog, poll_id)
    interaction.response.defer.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    assert_closed_edit(edit, 1)
    kwargs = send.await_args.kwargs
    assert kwargs["content"] == content.RESULT_PING.format(gm=f"<@{GM}>")
    assert pinged(kwargs) == [GM]
    assert kwargs["embed"].title
    buttons = kwargs["view"].children
    assert [b.custom_id for b in buttons] == [
        custom_id(poll_id, PollAction.CONFIRM, slots[1]),
        custom_id(poll_id, PollAction.CONFIRM, slots[0]),
    ]
    assert buttons[0].item.label.startswith("Confirm 1: ")
    assert kwargs["view"].is_persistent()
    assert (await store.get_poll(poll_id)).status == STATUS_AWAITING_CONFIRM  # type: ignore[union-attr]


async def test_close_by_a_player_is_refused(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, _ = await two_player_poll(cog, store)
    send, edit = channel_mocks(cog)
    interaction = await press_close(cog, poll_id, user_id=PLAYER)
    assert reply(interaction) == (content.NOT_ALLOWED_MESSAGE, {"ephemeral": True})
    send.assert_not_awaited()
    edit.assert_not_awaited()
    assert (await store.get_poll(poll_id)).status == STATUS_VOTING  # type: ignore[union-attr]


async def test_close_by_a_manager_is_allowed(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, _ = await two_player_poll(cog, store)
    send, _ = channel_mocks(cog)
    interaction = make_interaction(user_id=PLAYER)
    interaction.permissions = discord.Permissions(manage_events=True)
    await cog.handle_poll_action(interaction, poll_id, PollAction.CLOSE, None)
    interaction.response.defer.assert_awaited_once()
    send.assert_awaited_once()


async def test_double_close_is_harmless(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, slots = await two_player_poll(cog, store)
    send, _ = channel_mocks(cog)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    await cog.service.submit_vote(poll_id, PLAYER, slots, [])
    await press_close(cog, poll_id)
    second = await press_close(cog, poll_id)
    assert reply(second) == (content.ROUND_ALREADY_CLOSED_MESSAGE, {"ephemeral": True})
    send.assert_awaited_once()


async def test_close_next_round_posts_a_new_poll_and_pings_pending(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id, slots = await two_player_poll(cog, store)
    send, edit = channel_mocks(cog)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    await press_close(cog, poll_id)
    assert_closed_edit(edit, 1)
    kwargs = send.await_args.kwargs
    assert kwargs["content"] == content.NEXT_ROUND_PING.format(players=f"<@{PLAYER}>")
    assert pinged(kwargs) == [PLAYER]
    assert len(kwargs["embeds"]) == 2
    assert [b.custom_id for b in kwargs["view"].children][0] == custom_id(
        poll_id, PollAction.JOIN, None
    )
    poll = await store.get_poll(poll_id)
    assert poll is not None
    assert (poll.round_number, poll.status, poll.message_id) == (2, STATUS_VOTING, NEW_MESSAGE_ID)


async def test_next_round_without_pending_pings_nobody(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id, slots = await two_player_poll(cog, store)
    send, _ = channel_mocks(cog)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    await cog.service.submit_vote(poll_id, PLAYER, [], [])
    await press_close(cog, poll_id)
    kwargs = send.await_args.kwargs
    assert kwargs["content"] is None
    assert pinged(kwargs) == []


async def test_last_round_posts_no_fit_without_buttons(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id, _ = await two_player_poll(cog, store)
    send, edit = channel_mocks(cog)
    for _ in range(MAX_ROUNDS - 1):
        assert isinstance(await cog.close_and_post(poll_id, "manual", GM), RoundAdvanced)
    outcome = await cog.close_and_post(poll_id, "manual", GM)
    assert isinstance(outcome, RoundFailed)
    assert_closed_edit(edit, MAX_ROUNDS)
    kwargs = send.await_args.kwargs
    assert "view" not in kwargs
    assert kwargs["embed"].description.startswith(
        content.NO_FIT_DESCRIPTION.format(rounds=MAX_ROUNDS)
    )
    assert pinged(kwargs) == []
    assert (await store.get_poll(poll_id)).status == STATUS_FAILED  # type: ignore[union-attr]


async def test_failed_edit_or_send_does_not_abort(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, slots = await two_player_poll(cog, store)
    send, edit = channel_mocks(cog)
    edit.side_effect = discord.NotFound(MagicMock(status=404), "Unknown Message")
    send.side_effect = discord.Forbidden(MagicMock(status=403), "Missing Access")
    await cog.service.submit_vote(poll_id, GM, slots, [])
    interaction = await press_close(cog, poll_id)
    interaction.response.send_message.assert_not_awaited()
    send.assert_awaited_once()
    poll = await store.get_poll(poll_id)
    assert poll is not None
    # The round still advanced; no message id marks the new round's message as unposted.
    assert (poll.round_number, poll.message_id) == (2, None)


async def fitted_poll(cog: Sessions, store: SqliteSessionStore) -> tuple[int, list[int]]:
    poll_id, slots = await two_player_poll(cog, store)
    await cog.service.submit_vote(poll_id, GM, slots, [])
    await cog.service.submit_vote(poll_id, PLAYER, [], slots)
    await press_close(cog, poll_id)
    return poll_id, slots


async def test_confirm_edits_the_result_and_announces_the_roster(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    poll_id, slots = await fitted_poll(cog, store)
    send, _ = channel_mocks(cog)
    interaction = await press_confirm(cog, poll_id, slots[0])
    edit = interaction.response.edit_message.await_args.kwargs
    (slot,) = [s for s in await store.poll_slots(poll_id, 1) if s.id == slots[0]]
    assert edit["content"] == content.CONFIRMED_RESULT_MESSAGE.format(
        start=f"<t:{int(slot.start)}:F>"
    )
    assert edit["view"] is None
    kwargs = send.await_args.kwargs
    assert kwargs["content"] == content.SESSION_ANNOUNCEMENT.format(players=f"<@{GM}>, <@{PLAYER}>")
    assert pinged(kwargs) == [GM, PLAYER]
    assert kwargs["embed"].title
    assert (await store.get_poll(poll_id)).status == STATUS_CONFIRMED  # type: ignore[union-attr]


async def test_double_confirm_is_harmless(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, slots = await fitted_poll(cog, store)
    send, _ = channel_mocks(cog)
    await press_confirm(cog, poll_id, slots[0])
    second = await press_confirm(cog, poll_id, slots[1])
    assert reply(second) == (content.UNKNOWN_POLL_MESSAGE, {"ephemeral": True})
    second.response.edit_message.assert_not_awaited()
    send.assert_awaited_once()


async def test_confirm_by_a_player_is_refused(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, slots = await fitted_poll(cog, store)
    send, _ = channel_mocks(cog)
    interaction = await press_confirm(cog, poll_id, slots[0], user_id=PLAYER)
    assert reply(interaction) == (content.NOT_ALLOWED_MESSAGE, {"ephemeral": True})
    send.assert_not_awaited()
    assert (await store.get_poll(poll_id)).status == STATUS_AWAITING_CONFIRM  # type: ignore[union-attr]


async def test_confirm_while_voting_is_refused(cog: Sessions, store: SqliteSessionStore) -> None:
    poll_id, slots = await two_player_poll(cog, store)
    interaction = await press_confirm(cog, poll_id, slots[0])
    assert reply(interaction) == (content.NOT_AWAITING_CONFIRM_MESSAGE, {"ephemeral": True})


@pytest.mark.parametrize(
    ("action", "arg", "poll_id"),
    [(PollAction.CONFIRM, None, None), (PollAction.CLOSE, None, 9999)],
)
async def test_stale_close_and_confirm_buttons(
    cog: Sessions,
    store: SqliteSessionStore,
    action: PollAction,
    arg: int | None,
    poll_id: int | None,
) -> None:
    target = poll_id if poll_id is not None else await make_poll(cog, store)
    interaction = make_interaction()
    await cog.handle_poll_action(interaction, target, action, arg)
    assert reply(interaction) == (content.STALE_BUTTON_MESSAGE, {"ephemeral": True})


async def test_confirm_unknown_poll_replies_ephemerally(cog: Sessions) -> None:
    interaction = await press_confirm(cog, 9999, 1)
    assert reply(interaction) == (content.UNKNOWN_POLL_MESSAGE, {"ephemeral": True})


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


def test_poll_view_is_persistent_with_join_leave_vote_and_close() -> None:
    view = poll_view(5)
    assert view.timeout is None
    assert view.is_persistent()
    labels = [item.item.label for item in view.children if isinstance(item, PollButton)]
    assert labels == [
        content.JOIN_BUTTON,
        content.LEAVE_BUTTON,
        content.VOTE_BUTTON,
        content.CLOSE_ROUND_BUTTON,
    ]


def test_fixed_roster_poll_view_has_only_vote_and_close() -> None:
    view = poll_view(5, fixed_roster=True)
    labels = [item.item.label for item in view.children if isinstance(item, PollButton)]
    assert labels == [content.VOTE_BUTTON, content.CLOSE_ROUND_BUTTON]


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
    cog.bot.wait_until_ready = AsyncMock(side_effect=asyncio.Event().wait)  # type: ignore[attr-defined]
    await cog.cog_load()
    cog.bot.add_dynamic_items.assert_called_once_with(PollButton)  # type: ignore[attr-defined]
    await cog.cog_unload()


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


# exclude and players


async def test_poll_with_excluded_days_skips_them(cog: Sessions, store: SqliteSessionStore) -> None:
    interaction = make_interaction()
    await start_poll(
        cog,
        interaction,
        days="mon,tue,wed,thu,fri,sat,sun",
        weeks=1,
        start="2026-11-06",
        exclude="2026-11-07..2026-11-12",
    )
    _, kwargs = reply(interaction)
    (poll,) = await store.open_polls(GUILD)
    assert len(await store.poll_slots(poll.id, 1)) == 1
    assert "Skipping" in kwargs["embed"].description


async def test_bad_exclude_is_an_ephemeral_error(cog: Sessions, store: SqliteSessionStore) -> None:
    interaction = make_interaction()
    await start_poll(cog, interaction, exclude="soon")
    text, kwargs = reply(interaction)
    assert kwargs["ephemeral"] is True
    assert text == slots.INVALID_EXCLUDE_MESSAGE
    assert await store.open_polls(GUILD) == []


async def test_poll_with_players_pings_only_them_and_has_no_join_buttons(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    interaction = make_interaction()
    await start_poll(cog, interaction, players="<@2> <@3>")
    _, kwargs = reply(interaction)
    assert "<@2>" in kwargs["content"] and "<@3>" in kwargs["content"]
    assert sorted(user.id for user in kwargs["allowed_mentions"].users) == [2, 3]
    ids = [item.custom_id for item in kwargs["view"].children]
    assert not any(":join" in i or ":leave" in i for i in ids)
    assert any(":vote" in i for i in ids)
    (poll,) = await store.open_polls(GUILD)
    assert poll.fixed_roster
    assert sorted(await store.players(poll.id)) == [interaction.user.id, 2, 3]


async def test_bad_players_list_is_an_ephemeral_error(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    interaction = make_interaction()
    await start_poll(cog, interaction, players="Alice and Bob")
    text, kwargs = reply(interaction)
    assert kwargs["ephemeral"] is True
    assert text == INVALID_PLAYERS_MESSAGE
    assert await store.open_polls(GUILD) == []


async def test_old_join_button_on_a_fixed_poll_is_refused(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    await start_poll(cog, make_interaction(), players="<@2>")
    (poll,) = await store.open_polls(GUILD)
    click = make_interaction()
    click.user.id = 9
    await cog.handle_join(click, poll.id)
    text, kwargs = reply(click)
    assert text == content.FIXED_ROSTER_MESSAGE
    assert kwargs["ephemeral"] is True
    assert 9 not in await store.players(poll.id)


async def test_outsider_cannot_open_the_vote_form_of_a_fixed_poll(
    cog: Sessions, store: SqliteSessionStore
) -> None:
    await start_poll(cog, make_interaction(), players="<@2>")
    (poll,) = await store.open_polls(GUILD)
    click = make_interaction()
    click.user.id = 9
    await cog.handle_vote(click, poll.id)
    text, _ = reply(click)
    assert text == content.NOT_ON_LIST_MESSAGE
