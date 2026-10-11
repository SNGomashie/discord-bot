from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Iterable, Sequence

import discord
from discord import app_commands
from discord.ext import commands

from ninja_bot.help.commands import CommandInfo, ParamInfo
from ninja_bot.help.content import (
    COMMANDS,
    GENERIC_ERROR_MESSAGE,
    NO_SUGGESTIONS,
    PUBLIC_COOLDOWN_MESSAGE,
    SUGGESTIONS_TEXT,
    UNKNOWN_COMMAND_MESSAGE,
)
from ninja_bot.help.embed import build_command_embed, build_overview_embed
from ninja_bot.help.lookup import autocomplete_names, find_command, suggest
from ninja_bot.media.cooldown import UserCooldown

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
PUBLIC_COOLDOWN_SECONDS = 15.0


def command_infos(tree_commands: Iterable[object]) -> list[CommandInfo]:
    """Plain metadata of the top-level slash commands (groups and context menus skipped)."""
    infos: list[CommandInfo] = []
    for command in tree_commands:
        if not isinstance(command, app_commands.Command):
            continue
        params = tuple(
            ParamInfo(
                name=param.name,
                description=str(param.description),
                required=param.required,
                choices=tuple(str(choice.value) for choice in param.choices),
            )
            for param in command.parameters
        )
        infos.append(CommandInfo(command.name, str(command.description), params))
    return infos


def unknown_command_message(text: str, names: Sequence[str]) -> str:
    """The unknown-command reply with up to `MAX_SUGGESTIONS` similar names."""
    matches = suggest(text, names, MAX_SUGGESTIONS)
    if matches:
        suggestions = SUGGESTIONS_TEXT.format(names=", ".join(f"/{name}" for name in matches))
    else:
        suggestions = NO_SUGGESTIONS
    return UNKNOWN_COMMAND_MESSAGE.format(suggestions=suggestions)


def help_reply(infos: Sequence[CommandInfo], command: str | None) -> discord.Embed | str:
    """The overview embed, the detail embed for `command`, or the unknown-command text."""
    if command is None or not command.strip():
        return build_overview_embed(infos)
    by_name = {info.name: info for info in infos}
    name = find_command(command, list(by_name))
    if name is None:
        return unknown_command_message(command, list(by_name))
    entries = {entry.name: entry for entry in COMMANDS}
    return build_command_embed(by_name[name], entries.get(name))


def autocomplete_choices(current: str, names: Sequence[str]) -> list[app_commands.Choice[str]]:
    """Autocomplete choices for the `command` parameter (at most 25)."""
    return [
        app_commands.Choice(name=name, value=name) for name in autocomplete_names(current, names)
    ]


def public_cooldown_message(retry_after: float) -> str:
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return PUBLIC_COOLDOWN_MESSAGE.format(seconds=seconds, unit=unit)


async def _send_safely(send: Awaitable[object]) -> None:
    try:
        await send
    except discord.HTTPException:
        log.warning("Could not send help response", exc_info=True)


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Only public replies use it; taken once the command is resolved, refunded if not sent.
        self.public_cooldown = UserCooldown(PUBLIC_COOLDOWN_SECONDS)

    @app_commands.command(name="help", description="Show what the bot can do")
    @app_commands.describe(
        command="A command to explain, e.g. download",
        public="Post the help in the channel for everyone (default: only you see it)",
    )
    async def help(
        self, interaction: discord.Interaction, command: str | None = None, public: bool = False
    ) -> None:
        reply = help_reply(command_infos(self.bot.tree.get_commands()), command)
        none = discord.AllowedMentions.none()
        if not isinstance(reply, discord.Embed):
            # Unknown command: always private and never uses the public cooldown.
            await interaction.response.send_message(reply, ephemeral=True, allowed_mentions=none)
            return
        if not public:
            await interaction.response.send_message(
                embed=reply, ephemeral=True, allowed_mentions=none
            )
            return

        user_id = interaction.user.id
        retry_after = self.public_cooldown.try_acquire(user_id)
        if retry_after > 0:
            await interaction.response.send_message(
                public_cooldown_message(retry_after), ephemeral=True, allowed_mentions=none
            )
            return
        try:
            await interaction.response.send_message(
                embed=reply, ephemeral=False, allowed_mentions=none
            )
        except BaseException:
            self.public_cooldown.refund(user_id)
            raise

    @help.autocomplete("command")
    async def help_command_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        names = [info.name for info in command_infos(self.bot.tree.get_commands())]
        return autocomplete_choices(current, names)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Help cog", exc_info=error)
        if interaction.response.is_done():
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE, ephemeral=True))
        else:
            await _send_safely(
                interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
