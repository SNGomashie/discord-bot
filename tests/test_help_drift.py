"""Drift tests: /help content and the README must match the commands the bot registers.

The command set comes from loading the real extensions onto a `NinjaBot`, never from a
hand-written list. Everything here checks structure, not prose.
"""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import discord
import pytest
from discord import app_commands

from ninja_bot.bot import EXTENSIONS, NinjaBot
from ninja_bot.cogs.help import command_infos
from ninja_bot.config import Config
from ninja_bot.help.commands import CommandInfo
from ninja_bot.help.content import COMMANDS
from ninja_bot.help.embed import (
    EMBED_TOTAL_LIMIT,
    FIELD_VALUE_LIMIT,
    MAX_FIELDS,
    build_command_embed,
    build_overview_embed,
)

README = Path(__file__).resolve().parent.parent / "README.md"
COMMANDS_HEADING = "## Commands at a glance"
TABLE_ROW_RE = re.compile(r"^\|\s*`/([a-z0-9_-]+)`\s*\|")


@pytest.fixture
async def registered(
    tmp_path: Path,
) -> AsyncIterator[list[app_commands.Command[object, ..., object]]]:
    """The top-level slash commands registered by the real extensions (no login, no network)."""
    config = Config(
        environment="dev",
        token="test-token",
        guild_id=1,
        cs2_channel_id=None,
        data_dir=str(tmp_path / "data"),
    )
    bot = NinjaBot(config)
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    try:
        yield [
            command
            for command in bot.tree.get_commands()
            if isinstance(command, app_commands.Command)
        ]
    finally:
        for ext in reversed(EXTENSIONS):
            await bot.unload_extension(ext)


def registered_names(commands: list[app_commands.Command[object, ..., object]]) -> set[str]:
    return {command.name for command in commands}


def readme_command_rows() -> list[str]:
    """Command names from the first column of the README "Commands at a glance" table."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = lines.index(COMMANDS_HEADING)
    names: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        match = TABLE_ROW_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def embed_problems(embed: discord.Embed) -> list[str]:
    problems: list[str] = []
    if len(embed.fields) > MAX_FIELDS:
        problems.append(f"{len(embed.fields)} fields")
    for field in embed.fields:
        if len(str(field.value)) > FIELD_VALUE_LIMIT:
            problems.append(f"field {field.name!r} is {len(str(field.value))} chars")
    if len(embed) > EMBED_TOTAL_LIMIT:
        problems.append(f"total is {len(embed)} chars")
    return problems


async def test_extensions_register_commands(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    assert registered
    assert "help" in registered_names(registered)


async def test_every_registered_command_has_help(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    documented = {entry.name for entry in COMMANDS}
    missing = sorted(registered_names(registered) - documented)
    assert not missing, f"Registered commands without a CommandHelp entry: {missing}"


async def test_every_help_entry_names_a_registered_command(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    unknown = sorted({entry.name for entry in COMMANDS} - registered_names(registered))
    assert not unknown, f"CommandHelp entries for commands that don't exist: {unknown}"


async def test_parameter_notes_name_real_parameters(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    params = {command.name: {p.name for p in command.parameters} for command in registered}
    problems = [
        f"/{entry.name} {key}"
        for entry in COMMANDS
        if entry.name in params
        for key in entry.parameter_notes
        if key not in params[entry.name]
    ]
    assert not problems, f"parameter_notes for parameters that don't exist: {problems}"


async def test_discord_descriptions_are_not_empty(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    problems: list[str] = []
    for command in registered:
        if not str(command.description).strip():
            problems.append(f"/{command.name}")
        problems.extend(
            f"/{command.name} {param.name}"
            for param in command.parameters
            if not str(param.description).strip()
        )
    assert not problems, f"Missing Discord descriptions: {problems}"


async def test_readme_table_matches_registered_commands(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    rows = readme_command_rows()
    names = registered_names(registered)
    missing = sorted(names - set(rows))
    extra = sorted(set(rows) - names)
    duplicates = sorted({name for name in rows if rows.count(name) > 1})
    assert not missing, f"README 'Commands at a glance' has no row for: {missing}"
    assert not extra, f"README 'Commands at a glance' has rows for unknown commands: {extra}"
    assert not duplicates, f"README 'Commands at a glance' repeats: {duplicates}"


async def test_real_embeds_fit_discord_limits(
    registered: list[app_commands.Command[object, ..., object]],
) -> None:
    infos: list[CommandInfo] = command_infos(registered)
    entries = {entry.name: entry for entry in COMMANDS}
    overview = embed_problems(build_overview_embed(infos))
    assert not overview, f"Overview embed exceeds Discord limits: {overview}"
    for info in infos:
        problems = embed_problems(build_command_embed(info, entries.get(info.name)))
        assert not problems, f"/{info.name} help embed exceeds Discord limits: {problems}"


def test_readme_row_parser_reads_backticked_first_column() -> None:
    assert TABLE_ROW_RE.match("| `/ping` | none | public | none |")
    assert not TABLE_ROW_RE.match("| Command | Parameters | Reply | Cooldown (per user) |")
    assert not TABLE_ROW_RE.match("|---|---|---|---|")
