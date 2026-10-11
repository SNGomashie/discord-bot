"""Can the bot post CS2 auto-posts in a channel? Pure checks, no network calls."""

from __future__ import annotations

from typing import TypeGuard

import discord

# Channel types auto-posts can go to (all have guild permissions and `send`).
POST_CHANNEL_TYPES = (discord.TextChannel, discord.Thread, discord.VoiceChannel)
type PostChannel = discord.TextChannel | discord.Thread | discord.VoiceChannel
# Causes for a skipped auto-post cycle; each is logged once until the channel works again.
CHANNEL_MISSING = "missing"
CHANNEL_NOT_TEXT = "not a text channel"
CHANNEL_NO_PERMISSIONS = "missing View Channel, Send Messages or Embed Links"


def is_post_channel(channel: object) -> TypeGuard[PostChannel]:
    return isinstance(channel, POST_CHANNEL_TYPES)


def channel_problem(channel: object, me: discord.Member) -> str | None:
    """Why `me` can't post auto-posts in `channel`, or None if it can.

    A missing channel (`CHANNEL_MISSING`) is decided where the channel is fetched.
    """
    if not is_post_channel(channel):
        return CHANNEL_NOT_TEXT
    permissions = channel.permissions_for(me)
    can_send = (
        permissions.send_messages_in_threads
        if isinstance(channel, discord.Thread)
        else permissions.send_messages
    )
    if not (permissions.view_channel and can_send and permissions.embed_links):
        return CHANNEL_NO_PERMISSIONS
    return None
