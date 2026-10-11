"""Shared auto-post helpers: channel resolution, once-per-cause warnings, safe sends."""

import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ninja_bot.cs2.channels import (
    CHANNEL_MISSING,
    CHANNEL_NO_PERMISSIONS,
    CHANNEL_NOT_TEXT,
    CHANNEL_UNAVAILABLE,
)
from ninja_bot.posting import PostChannelResolver, send_safely

CHANNEL_ID = 555
TEMPLATE = "Not posting test posts: the TEST_CHANNEL_ID channel is %s"
ALL_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)


def make_channel(permissions: discord.Permissions = ALL_PERMISSIONS) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    channel.permissions_for.return_value = permissions
    return channel


def make_bot(channel: object | None, fetch_error: Exception | None = None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    if fetch_error is None:
        fetch_error = discord.NotFound(MagicMock(status=404), "Unknown Channel")
    bot.fetch_channel = AsyncMock(side_effect=fetch_error)
    return bot


def warning(cause: str) -> str:
    return TEMPLATE % cause


async def test_cached_channel_is_returned() -> None:
    channel = make_channel()
    bot = make_bot(channel)
    assert await PostChannelResolver(bot, TEMPLATE, CHANNEL_ID).resolve() is channel
    bot.get_channel.assert_called_once_with(CHANNEL_ID)
    bot.fetch_channel.assert_not_called()


async def test_uncached_channel_is_fetched() -> None:
    channel = make_channel()
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(return_value=channel)
    assert await PostChannelResolver(bot, TEMPLATE, CHANNEL_ID).resolve() is channel
    bot.fetch_channel.assert_awaited_once_with(CHANNEL_ID)


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (discord.NotFound(MagicMock(status=404), "Unknown Channel"), CHANNEL_MISSING),
        (discord.Forbidden(MagicMock(status=403), "Missing Access"), CHANNEL_MISSING),
        (discord.HTTPException(MagicMock(status=503), "Service Unavailable"), CHANNEL_UNAVAILABLE),
    ],
)
async def test_fetch_errors_warn_once(
    caplog: pytest.LogCaptureFixture, error: Exception, cause: str
) -> None:
    resolver = PostChannelResolver(make_bot(None, error), TEMPLATE, CHANNEL_ID)
    with caplog.at_level(logging.WARNING):
        assert await resolver.resolve() is None
        assert await resolver.resolve() is None
    assert caplog.text.count(warning(cause)) == 1
    assert all(record.levelno == logging.WARNING for record in caplog.records)


async def test_wrong_channel_type_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    resolver = PostChannelResolver(
        make_bot(MagicMock(spec=discord.CategoryChannel)), TEMPLATE, CHANNEL_ID
    )
    with caplog.at_level(logging.WARNING):
        assert await resolver.resolve() is None
        assert await resolver.resolve() is None
    assert caplog.text.count(warning(CHANNEL_NOT_TEXT)) == 1


async def test_missing_permissions_warn_once(caplog: pytest.LogCaptureFixture) -> None:
    channel = make_channel(discord.Permissions(view_channel=True, send_messages=True))
    resolver = PostChannelResolver(make_bot(channel), TEMPLATE, CHANNEL_ID)
    with caplog.at_level(logging.WARNING):
        assert await resolver.resolve() is None
        assert await resolver.resolve() is None
    assert caplog.text.count(warning(CHANNEL_NO_PERMISSIONS)) == 1


async def test_different_causes_each_warn(caplog: pytest.LogCaptureFixture) -> None:
    bot = make_bot(None)
    resolver = PostChannelResolver(bot, TEMPLATE, CHANNEL_ID)
    with caplog.at_level(logging.WARNING):
        await resolver.resolve()
        bot.get_channel.return_value = MagicMock(spec=discord.CategoryChannel)
        await resolver.resolve()
    assert caplog.text.count(warning(CHANNEL_MISSING)) == 1
    assert caplog.text.count(warning(CHANNEL_NOT_TEXT)) == 1


async def test_working_channel_clears_the_warnings(caplog: pytest.LogCaptureFixture) -> None:
    bot = make_bot(None)
    resolver = PostChannelResolver(bot, TEMPLATE, CHANNEL_ID)
    with caplog.at_level(logging.WARNING):
        await resolver.resolve()
        bot.get_channel.return_value = make_channel()
        assert await resolver.resolve() is not None
        bot.get_channel.return_value = None
        await resolver.resolve()
    assert caplog.text.count(warning(CHANNEL_MISSING)) == 2


async def test_send_safely_reports_success() -> None:
    send = AsyncMock()
    assert await send_safely(send()) is True


async def test_send_safely_logs_and_swallows_http_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "boom"))
    with caplog.at_level(logging.WARNING):
        assert await send_safely(send(), "test response") is False
    assert "Could not send test response" in caplog.text


async def test_send_safely_lets_other_errors_through() -> None:
    send = AsyncMock(side_effect=RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        await send_safely(send())
