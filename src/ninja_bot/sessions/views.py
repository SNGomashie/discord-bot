"""Session poll buttons; the only module importing `discord.ui`.

Buttons are `DynamicItem`s whose `custom_id` holds the poll id and the action, so they
keep working after a restart. Every click is handed to the Sessions cog
(`PollActionHandler.handle_poll_action`), which holds all the logic.
"""

from __future__ import annotations

from enum import StrEnum
from re import Match
from typing import Protocol, runtime_checkable

import discord
from discord import ui
from discord.ext import commands

from ninja_bot.sessions import content

CUSTOM_ID_TEMPLATE = r"ses:(?P<poll>\d+):(?P<action>[a-z]+)(?::(?P<arg>\d+))?"
# The cog that handles clicks (`commands.Cog` names default to the class name).
HANDLER_COG = "Sessions"


class PollAction(StrEnum):
    JOIN = "join"
    LEAVE = "leave"


def custom_id(poll_id: int, action: str, arg: int | None = None) -> str:
    """`ses:<poll>:<action>[:<arg>]`, matching `CUSTOM_ID_TEMPLATE`."""
    suffix = "" if arg is None else f":{arg}"
    return f"ses:{poll_id}:{action}{suffix}"


@runtime_checkable
class PollActionHandler(Protocol):
    async def handle_poll_action(
        self, interaction: discord.Interaction, poll_id: int, action: str, arg: int | None
    ) -> None: ...


class PollButton(ui.DynamicItem[ui.Button[ui.View]], template=CUSTOM_ID_TEMPLATE):
    """A poll button; the action and its optional argument live in the `custom_id`."""

    def __init__(
        self,
        poll_id: int,
        action: str,
        arg: int | None = None,
        *,
        label: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(
            ui.Button(label=label, style=style, custom_id=custom_id(poll_id, action, arg))
        )
        self.poll_id = poll_id
        self.action = action
        self.arg = arg

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Item[ui.View],
        match: Match[str],
        /,
    ) -> PollButton:
        return cls.from_match(match)

    @classmethod
    def from_match(cls, match: Match[str]) -> PollButton:
        arg = match["arg"]
        return cls(int(match["poll"]), match["action"], None if arg is None else int(arg))

    async def callback(self, interaction: discord.Interaction) -> None:
        client = interaction.client
        handler = client.get_cog(HANDLER_COG) if isinstance(client, commands.Bot) else None
        if not isinstance(handler, PollActionHandler):
            await interaction.response.send_message(content.STALE_BUTTON_MESSAGE, ephemeral=True)
            return
        await handler.handle_poll_action(interaction, self.poll_id, self.action, self.arg)


def poll_view(poll_id: int) -> ui.View:
    """The buttons under a voting poll message."""
    view = ui.View(timeout=None)
    view.add_item(
        PollButton(
            poll_id, PollAction.JOIN, label=content.JOIN_BUTTON, style=discord.ButtonStyle.success
        )
    )
    view.add_item(PollButton(poll_id, PollAction.LEAVE, label=content.LEAVE_BUTTON))
    return view
