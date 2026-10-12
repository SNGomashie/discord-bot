"""Session poll buttons; the only module importing `discord.ui`.

Buttons are `DynamicItem`s whose `custom_id` holds the poll id and the action, so they
keep working after a restart. Every click is handed to the Sessions cog
(`PollActionHandler.handle_poll_action`), which holds all the logic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from enum import StrEnum
from re import Match
from typing import Literal, Protocol, runtime_checkable

import discord
from discord import ui
from discord.ext import commands

from ninja_bot.sessions import content

CUSTOM_ID_TEMPLATE = r"ses:(?P<poll>\d+):(?P<action>[a-z]+)(?::(?P<arg>\d+))?"
# Discord's limit on a button label.
BUTTON_LABEL_LIMIT = 80
# The cog that handles clicks (`commands.Cog` names default to the class name).
HANDLER_COG = "Sessions"


class PollAction(StrEnum):
    JOIN = "join"
    LEAVE = "leave"
    VOTE = "vote"
    CLOSE = "close"
    # The argument is the offered slot's id.
    CONFIRM = "confirm"


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


def poll_view(poll_id: int, fixed_roster: bool = False) -> ui.View:
    """The buttons under a voting poll message; no Join / Leave for a fixed player list."""
    view = ui.View(timeout=None)
    if not fixed_roster:
        view.add_item(
            PollButton(
                poll_id,
                PollAction.JOIN,
                label=content.JOIN_BUTTON,
                style=discord.ButtonStyle.success,
            )
        )
        view.add_item(PollButton(poll_id, PollAction.LEAVE, label=content.LEAVE_BUTTON))
    view.add_item(
        PollButton(
            poll_id, PollAction.VOTE, label=content.VOTE_BUTTON, style=discord.ButtonStyle.primary
        )
    )
    view.add_item(
        PollButton(
            poll_id,
            PollAction.CLOSE,
            label=content.CLOSE_ROUND_BUTTON,
            style=discord.ButtonStyle.danger,
        )
    )
    return view


def confirm_view(poll_id: int, options: Sequence[tuple[int, str]]) -> ui.View:
    """One persistent **Confirm** button per offered slot ((slot id, label) pairs, best first)."""
    view = ui.View(timeout=None)
    for rank, (slot_id, label) in enumerate(options, start=1):
        text = content.CONFIRM_BUTTON.format(rank=rank, label=label)
        view.add_item(
            PollButton(
                poll_id,
                PollAction.CONFIRM,
                slot_id,
                label=text[:BUTTON_LABEL_LIMIT],
                style=discord.ButtonStyle.success,
            )
        )
    return view


# The private vote view: how long its selects and buttons stay usable.
VOTE_VIEW_TIMEOUT_SECONDS = 600.0
# (interaction, yes slot ids, if-needed slot ids); `([], [])` is "none of these".
SaveVote = Callable[[discord.Interaction, list[int], list[int]], Awaitable[None]]
VoteKind = Literal["yes", "maybe"]


class _SlotSelect(ui.Select["VoteView"]):
    """A multi-select over the round's slots; the picked ids are kept on the view."""

    def __init__(
        self,
        kind: VoteKind,
        placeholder: str,
        options: Sequence[tuple[int, str]],
        selected: Collection[int],
        row: int,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=len(options),
            options=[
                discord.SelectOption(label=label, value=str(slot_id), default=slot_id in selected)
                for slot_id, label in options
            ],
            row=row,
        )
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            self.view.pick(self.kind, [int(value) for value in self.values])
        # Nothing to show yet; the choice is saved with Save.
        await interaction.response.defer()


class VoteView(ui.View):
    """Private vote form: **Works for me** / **Only if needed** selects, Save, None of these.

    `options` are (slot id, label) pairs; the selects start with the user's stored votes.
    Both buttons hand the choice to `on_save` and stop the view, except a Save with nothing
    picked: that is refused privately and the form stays open, so a too-early Save never
    counts as "none of these".
    """

    def __init__(
        self,
        options: Sequence[tuple[int, str]],
        yes_ids: Collection[int],
        maybe_ids: Collection[int],
        on_save: SaveVote,
        *,
        timeout: float = VOTE_VIEW_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(timeout=timeout)
        self.on_save = on_save
        self.yes_ids = sorted(yes_ids)
        self.maybe_ids = sorted(maybe_ids)
        self.yes_select = _SlotSelect("yes", content.VOTE_YES_PLACEHOLDER, options, yes_ids, 0)
        self.maybe_select = _SlotSelect(
            "maybe", content.VOTE_MAYBE_PLACEHOLDER, options, maybe_ids, 1
        )
        self.add_item(self.yes_select)
        self.add_item(self.maybe_select)

    def pick(self, kind: VoteKind, slot_ids: list[int]) -> None:
        if kind == "yes":
            self.yes_ids = sorted(slot_ids)
        else:
            self.maybe_ids = sorted(slot_ids)

    @ui.button(label=content.VOTE_SAVE_BUTTON, style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, button: ui.Button[VoteView]) -> None:
        if not self.yes_ids and not self.maybe_ids:
            await interaction.response.send_message(content.VOTE_EMPTY_MESSAGE, ephemeral=True)
            return
        self.stop()
        await self.on_save(interaction, self.yes_ids, self.maybe_ids)

    @ui.button(label=content.VOTE_NONE_BUTTON, style=discord.ButtonStyle.danger, row=2)
    async def none_of_these(
        self, interaction: discord.Interaction, button: ui.Button[VoteView]
    ) -> None:
        self.stop()
        await self.on_save(interaction, [], [])
