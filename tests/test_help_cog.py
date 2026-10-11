from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from ninja_bot.bot import EXTENSIONS
from ninja_bot.cogs.core import Core
from ninja_bot.cogs.cs2 import Cs2
from ninja_bot.cogs.help import (
    PUBLIC_COOLDOWN_SECONDS,
    Help,
    autocomplete_choices,
    command_infos,
    public_cooldown_message,
    unknown_command_message,
)
from ninja_bot.cogs.media import Media
from ninja_bot.help.commands import CommandInfo, ParamInfo
from ninja_bot.help.content import (
    GENERIC_ERROR_MESSAGE,
    NO_SUGGESTIONS,
    OVERVIEW_TITLE,
    PUBLIC_COOLDOWN_MESSAGE,
    SUGGESTIONS_TEXT,
    UNKNOWN_COMMAND_MESSAGE,
)


def tree_commands() -> list[Any]:
    """The real command objects of every cog, as `bot.tree.get_commands()` returns them."""
    return [command for cog in (Core, Media, Cs2, Help) for command in cog.__cog_app_commands__]


def make_cog(commands: list[Any] | None = None) -> Help:
    bot = MagicMock()
    bot.tree.get_commands = MagicMock(
        return_value=tree_commands() if commands is None else commands
    )
    return Help(bot)


def make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild = None
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def reply(interaction: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    return args, kwargs


def assert_private(kwargs: dict[str, Any]) -> None:
    assert kwargs["ephemeral"] is True
    mentions = kwargs["allowed_mentions"]
    assert isinstance(mentions, discord.AllowedMentions)
    assert mentions.everyone is False
    assert mentions.users is False
    assert mentions.roles is False
    assert mentions.replied_user is False


def test_help_registered_in_extensions() -> None:
    assert "ninja_bot.cogs.help" in EXTENSIONS


def test_help_command_metadata() -> None:
    command = Help.help
    assert command.name == "help"
    assert command.description == "Show what the bot can do"
    param = command._params["command"]
    assert param.required is False
    assert str(param.description) == "A command to explain, e.g. download"


def test_command_infos_from_tree() -> None:
    infos = {info.name: info for info in command_infos(tree_commands())}
    assert set(infos) == {
        "ping",
        "download",
        "link",
        "unlink",
        "scoreboard",
        "profile",
        "leaderboard",
        "help",
    }
    download = infos["download"]
    assert download.description == "Download a video as mp3 or mp4"
    params = {param.name: param for param in download.params}
    assert params["url"].required is True
    assert params["format"].required is False
    assert params["format"].choices == ("mp3", "mp4")
    assert infos["ping"].params == ()


def test_command_infos_skips_groups() -> None:
    group = app_commands.Group(name="grp", description="A group")
    infos = command_infos([group, *tree_commands()])
    assert "grp" not in {info.name for info in infos}


async def test_overview_reply_is_private() -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction)
    args, kwargs = reply(interaction)
    assert args == ()
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.title == OVERVIEW_TITLE
    assert_private(kwargs)


@pytest.mark.parametrize("text", ["download", "/Download", "  DOWNLOAD "])
async def test_detail_reply_for_known_command(text: str) -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, text)
    _, kwargs = reply(interaction)
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.title == "/download"
    assert_private(kwargs)


async def test_blank_command_shows_overview() -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, "   ")
    _, kwargs = reply(interaction)
    assert kwargs["embed"].title == OVERVIEW_TITLE


async def test_unknown_command_gets_suggestions() -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, "l")
    args, kwargs = reply(interaction)
    expected = UNKNOWN_COMMAND_MESSAGE.format(
        suggestions=SUGGESTIONS_TEXT.format(names="/leaderboard, /link, /download")
    )
    assert args == (expected,)
    assert "embed" not in kwargs
    assert_private(kwargs)


async def test_unknown_command_without_suggestions() -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, "zzz")
    args, kwargs = reply(interaction)
    assert args == (UNKNOWN_COMMAND_MESSAGE.format(suggestions=NO_SUGGESTIONS),)
    assert_private(kwargs)


def test_unknown_command_message_caps_suggestions() -> None:
    names = ["aa", "ab", "ac", "ad"]
    message = unknown_command_message("a", names)
    assert message == UNKNOWN_COMMAND_MESSAGE.format(
        suggestions=SUGGESTIONS_TEXT.format(names="/aa, /ab, /ac")
    )


async def test_command_without_help_entry_uses_discord_description() -> None:
    extra = app_commands.Command(name="dice", description="Roll a dice", callback=_dice)
    cog = make_cog([*tree_commands(), extra])
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, "dice")
    _, kwargs = reply(interaction)
    assert kwargs["embed"].title == "/dice"
    assert kwargs["embed"].description == "Roll a dice"


async def _dice(interaction: discord.Interaction) -> None:
    return None


def test_command_info_shape() -> None:
    info = command_infos([Core.ping])[0]
    assert info == CommandInfo("ping", "Check that the bot is alive", ())
    assert isinstance(ParamInfo("x", "y", True), ParamInfo)


@pytest.mark.parametrize("done", [False, True])
async def test_cog_app_command_error_is_private_and_logged(
    done: bool, caplog: pytest.LogCaptureFixture
) -> None:
    cog = make_cog()
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=done)
    await cog.cog_app_command_error(interaction, app_commands.AppCommandError("x"))
    send = interaction.followup.send if done else interaction.response.send_message
    send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE, ephemeral=True)
    assert "App command error in Help cog" in caplog.text


async def test_cog_app_command_error_send_failure_is_swallowed() -> None:
    cog = make_cog()
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=False)
    error = discord.HTTPException(MagicMock(status=500, reason="boom"), "boom")
    interaction.response.send_message = AsyncMock(side_effect=error)
    await cog.cog_app_command_error(interaction, app_commands.AppCommandError("x"))


def test_public_parameter_metadata() -> None:
    param = Help.help._params["public"]
    assert param.required is False
    assert param.default is False
    assert param.type is discord.AppCommandOptionType.boolean
    assert str(param.description)


async def test_autocomplete_partial_string() -> None:
    cog = make_cog()
    choices = await cog.help_command_autocomplete(make_interaction(), "pro")
    assert choices == [app_commands.Choice(name="profile", value="profile")]


async def test_autocomplete_empty_input_lists_every_command() -> None:
    cog = make_cog()
    choices = await cog.help_command_autocomplete(make_interaction(), "")
    names = {choice.value for choice in choices}
    assert names == {info.name for info in command_infos(tree_commands())}


def test_autocomplete_choices_capped_at_25() -> None:
    names = [f"cmd{i:02d}" for i in range(40)]
    choices = autocomplete_choices("", names)
    assert len(choices) == 25
    assert all(choice.name == choice.value for choice in choices)


def test_command_param_has_autocomplete() -> None:
    assert Help.help._params["command"].autocomplete is not None


async def test_public_reply_is_not_ephemeral() -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, "download", True)
    _, kwargs = reply(interaction)
    assert kwargs["embed"].title == "/download"
    assert kwargs["ephemeral"] is False
    assert kwargs["allowed_mentions"].everyone is False


async def test_public_cooldown_throttles_second_public_call() -> None:
    cog = make_cog()
    await Help.help.callback(cog, make_interaction(), None, True)
    second = make_interaction()
    await Help.help.callback(cog, second, None, True)
    args, kwargs = reply(second)
    assert "embed" not in kwargs
    assert args == (public_cooldown_message(PUBLIC_COOLDOWN_SECONDS),)
    assert_private(kwargs)


async def test_public_cooldown_does_not_block_private_call() -> None:
    cog = make_cog()
    await Help.help.callback(cog, make_interaction(), None, True)
    private = make_interaction()
    await Help.help.callback(cog, private, None, False)
    _, kwargs = reply(private)
    assert kwargs["embed"].title == OVERVIEW_TITLE
    assert_private(kwargs)


async def test_private_calls_do_not_take_public_cooldown() -> None:
    cog = make_cog()
    await Help.help.callback(cog, make_interaction(), None, False)
    assert cog.public_cooldown.try_acquire(42) == 0.0


async def test_unknown_command_public_does_not_take_cooldown() -> None:
    cog = make_cog()
    interaction = make_interaction()
    await Help.help.callback(cog, interaction, "zzz", True)
    args, kwargs = reply(interaction)
    assert args == (UNKNOWN_COMMAND_MESSAGE.format(suggestions=NO_SUGGESTIONS),)
    assert_private(kwargs)
    assert cog.public_cooldown.try_acquire(42) == 0.0


async def test_failing_public_send_refunds_cooldown() -> None:
    cog = make_cog()
    interaction = make_interaction()
    error = discord.HTTPException(MagicMock(status=500, reason="boom"), "boom")
    interaction.response.send_message = AsyncMock(side_effect=error)
    with pytest.raises(discord.HTTPException):
        await Help.help.callback(cog, interaction, None, True)
    assert cog.public_cooldown.try_acquire(42) == 0.0


@pytest.mark.parametrize(
    ("retry_after", "seconds", "unit"),
    [(14.2, 15, "seconds"), (0.3, 1, "second"), (1.0, 1, "second")],
)
def test_public_cooldown_message_rounds_up(retry_after: float, seconds: int, unit: str) -> None:
    expected = PUBLIC_COOLDOWN_MESSAGE.format(seconds=seconds, unit=unit)
    assert public_cooldown_message(retry_after) == expected
