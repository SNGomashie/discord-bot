from unittest.mock import MagicMock

import discord
import pytest

from ninja_bot.cs2.channels import (
    CHANNEL_NO_PERMISSIONS,
    CHANNEL_NOT_TEXT,
    channel_problem,
    is_post_channel,
)

ALL_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
THREAD_PERMISSIONS = discord.Permissions(
    view_channel=True, send_messages_in_threads=True, embed_links=True
)


def make_channel(permissions: discord.Permissions, spec: type = discord.TextChannel) -> MagicMock:
    channel = MagicMock(spec=spec)
    channel.guild = MagicMock()
    channel.permissions_for.return_value = permissions
    return channel


def test_text_channel_with_every_permission() -> None:
    me = MagicMock()
    assert channel_problem(make_channel(ALL_PERMISSIONS), me) is None


@pytest.mark.parametrize("missing", ["view_channel", "send_messages", "embed_links"])
def test_each_missing_permission(missing: str) -> None:
    permissions = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
    setattr(permissions, missing, False)
    assert channel_problem(make_channel(permissions), MagicMock()) == CHANNEL_NO_PERMISSIONS


def test_thread_needs_send_in_threads() -> None:
    me = MagicMock()
    assert (
        channel_problem(make_channel(ALL_PERMISSIONS, discord.Thread), me) == CHANNEL_NO_PERMISSIONS
    )
    assert channel_problem(make_channel(THREAD_PERMISSIONS, discord.Thread), me) is None


def test_text_channel_ignores_send_in_threads() -> None:
    assert channel_problem(make_channel(THREAD_PERMISSIONS), MagicMock()) == CHANNEL_NO_PERMISSIONS


def test_voice_channel() -> None:
    me = MagicMock()
    assert channel_problem(make_channel(ALL_PERMISSIONS, discord.VoiceChannel), me) is None
    assert (
        channel_problem(make_channel(discord.Permissions(), discord.VoiceChannel), me)
        == CHANNEL_NO_PERMISSIONS
    )


def test_category_channel_is_not_text() -> None:
    channel = make_channel(ALL_PERMISSIONS, discord.CategoryChannel)
    assert not is_post_channel(channel)
    assert channel_problem(channel, MagicMock()) == CHANNEL_NOT_TEXT
    channel.permissions_for.assert_not_called()


def test_permissions_are_checked_for_me() -> None:
    channel = make_channel(ALL_PERMISSIONS)
    me = MagicMock()
    assert me is not channel.guild.me
    assert channel_problem(channel, me) is None
    channel.permissions_for.assert_called_once_with(me)
