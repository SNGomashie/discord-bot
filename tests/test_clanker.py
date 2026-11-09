import random
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ninja_bot.clanker.content import CLANKER_REPLIES, TIMEOUT_REPLIES
from ninja_bot.clanker.logic import (
    TIMEOUT_SECONDS,
    ClankerCooldown,
    ReplyTracker,
    mentions_clanker,
)
from ninja_bot.cogs.clanker import Clanker, setup
from ninja_bot.config import load_config
from ninja_bot.flavor import LinePicker

PROD = {"ENVIRONMENT": "prod", "DISCORD_TOKEN": "t"}


@pytest.mark.parametrize(
    "text",
    [
        "clanker",
        "You CLANKER",
        "stupid Clanker!",
        "clankers everywhere",
        "<@123> clanker?",
        "a,clanker.",
    ],
)
def test_matches(text: str) -> None:
    assert mentions_clanker(text)


@pytest.mark.parametrize("text", ["", "clankerous", "slanker", "hello", "clank er"])
def test_no_match(text: str) -> None:
    assert not mentions_clanker(text)


def test_cooldown_per_channel_and_expiry() -> None:
    now = [0.0]
    cooldown = ClankerCooldown(60, lambda: now[0])
    assert cooldown.allow(1)
    assert not cooldown.allow(1)
    assert cooldown.allow(2)
    now[0] = 61
    assert cooldown.allow(1)


def test_cooldown_stays_bounded() -> None:
    cooldown = ClankerCooldown(60, lambda: 0.0)
    for channel in range(5000):
        cooldown.allow(channel)
    assert len(cooldown._last) <= 1001


def test_replies_pool_is_big_and_unique() -> None:
    assert len(CLANKER_REPLIES) >= 25
    assert len(set(CLANKER_REPLIES)) == len(CLANKER_REPLIES)
    for line in CLANKER_REPLIES:
        line.format()


def test_timeout_replies_pool() -> None:
    assert len(TIMEOUT_REPLIES) >= 8
    assert len(set(TIMEOUT_REPLIES)) == len(TIMEOUT_REPLIES)


def test_reply_tracker_is_bounded_and_ignores_unknown() -> None:
    tracker = ReplyTracker(limit=3)
    for message_id in range(1, 6):
        tracker.remember(message_id)
    assert not tracker.is_reply_to_ours(1)
    assert tracker.is_reply_to_ours(5)
    assert not tracker.is_reply_to_ours(None)


def make_message(content: str, *, bot: bool = False, guild_id: int | None = 1) -> MagicMock:
    message = MagicMock()
    message.content = content
    message.author.bot = bot
    message.channel.id = 9
    message.guild = None if guild_id is None else MagicMock(id=guild_id)
    message.reference = None
    message.reply = AsyncMock(return_value=MagicMock(id=900))
    return message


def make_cog() -> Clanker:
    return Clanker(1, LinePicker(random.Random(0)), ClankerCooldown(60, lambda: 0.0))


async def test_replies_without_pinging() -> None:
    message = make_message("such a clanker")
    await make_cog().on_message(message)
    message.reply.assert_awaited_once()
    args, kwargs = message.reply.await_args
    assert args[0] in CLANKER_REPLIES
    assert kwargs["mention_author"] is False
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


@pytest.mark.parametrize(
    "message",
    [
        make_message("clanker", bot=True),
        make_message("clanker", guild_id=None),
        make_message("clanker", guild_id=2),
        make_message("nothing to see"),
    ],
)
async def test_ignored_messages(message: MagicMock) -> None:
    await make_cog().on_message(message)
    message.reply.assert_not_awaited()


async def test_cooldown_blocks_second_reply() -> None:
    cog = make_cog()
    first, second = make_message("clanker"), make_message("clanker")
    await cog.on_message(first)
    await cog.on_message(second)
    first.reply.assert_awaited_once()
    second.reply.assert_not_awaited()


async def test_http_error_is_swallowed() -> None:
    message = make_message("clanker")
    message.reply.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
    await make_cog().on_message(message)


@pytest.mark.parametrize(("flag", "added"), [(None, False), ("true", True)])
async def test_setup_only_with_flag(flag: str | None, added: bool) -> None:
    env = dict(PROD) if flag is None else {**PROD, "CLANKER_REPLIES": flag}
    bot = MagicMock()
    bot.config = load_config(env)
    bot.add_cog = AsyncMock()
    await setup(bot)
    assert bot.add_cog.await_count == int(added)


def make_answer(to_id: int) -> MagicMock:
    message = make_message("lol whatever")
    message.author = MagicMock(spec=discord.Member)
    message.author.bot = False
    message.author.timeout = AsyncMock()
    message.reference = MagicMock(message_id=to_id)
    return message


async def test_answering_a_clanker_reply_gets_timed_out() -> None:
    cog = make_cog()
    await cog.on_message(make_message("clanker"))  # the bot's reply gets id 900
    answer = make_answer(900)
    await cog.on_message(answer)
    answer.author.timeout.assert_awaited_once()
    assert answer.author.timeout.await_args.args[0].total_seconds() == TIMEOUT_SECONDS
    args, kwargs = answer.reply.await_args
    assert args[0] in TIMEOUT_REPLIES
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


async def test_replying_to_other_messages_is_fine() -> None:
    cog = make_cog()
    await cog.on_message(make_message("clanker"))
    answer = make_answer(12345)
    await cog.on_message(answer)
    answer.author.timeout.assert_not_awaited()
    answer.reply.assert_not_awaited()


async def test_timeout_failure_still_taunts() -> None:
    cog = make_cog()
    await cog.on_message(make_message("clanker"))
    answer = make_answer(900)
    answer.author.timeout.side_effect = discord.Forbidden(MagicMock(status=403), "no")
    await cog.on_message(answer)
    answer.reply.assert_awaited_once()


async def test_bot_replies_are_never_punished() -> None:
    cog = make_cog()
    await cog.on_message(make_message("clanker"))
    answer = make_answer(900)
    answer.author.bot = True
    await cog.on_message(answer)
    answer.author.timeout.assert_not_awaited()


async def test_failed_reply_is_not_tracked() -> None:
    cog = make_cog()
    first = make_message("clanker")
    first.reply.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
    await cog.on_message(first)
    answer = make_answer(900)
    await cog.on_message(answer)
    answer.author.timeout.assert_not_awaited()
