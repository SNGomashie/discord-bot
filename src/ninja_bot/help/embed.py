"""Discord embeds for /help. All text comes from `help/content.py`."""

from __future__ import annotations

from collections.abc import Sequence

import discord

from ninja_bot.help.commands import CommandInfo, usage_line
from ninja_bot.help.content import (
    COMMANDS,
    FOOTER,
    GETTING_STARTED,
    GETTING_STARTED_TITLE,
    GROUPS,
    OTHER_GROUP_TITLE,
    OVERVIEW_INTRO,
    OVERVIEW_TITLE,
)

# Discord embed limits.
FIELD_VALUE_LIMIT = 1024
EMBED_TOTAL_LIMIT = 6000
MAX_FIELDS = 25

HELP_COLOUR = discord.Colour.blurple()


def build_overview_embed(commands: Sequence[CommandInfo]) -> discord.Embed:
    """Every registered command, grouped as in `GROUPS`, with its usage line and summary.

    Help entries for commands that aren't registered are skipped; registered commands
    without an entry (or with an unknown group) go in a trailing "Other" group.
    """
    registered = {info.name: info for info in commands}
    group_keys = {group.key for group in GROUPS}
    lines: dict[str, list[str]] = {group.key: [] for group in GROUPS}
    documented: set[str] = set()
    other: list[str] = []
    for entry in COMMANDS:
        info = registered.get(entry.name)
        if info is None:
            continue
        documented.add(entry.name)
        line = _command_line(info, entry.summary)
        if entry.group in group_keys:
            lines[entry.group].append(line)
        else:
            other.append(line)
    other.extend(
        _command_line(info, info.description) for info in commands if info.name not in documented
    )

    embed = discord.Embed(title=OVERVIEW_TITLE, description=OVERVIEW_INTRO, colour=HELP_COLOUR)
    embed.add_field(name=GETTING_STARTED_TITLE, value=GETTING_STARTED, inline=False)
    sections = [(group.title, lines[group.key]) for group in GROUPS]
    sections.append((OTHER_GROUP_TITLE, other))
    for title, group_lines in sections:
        if group_lines and len(embed.fields) < MAX_FIELDS:
            value = _fit_lines(group_lines, FIELD_VALUE_LIMIT)
            embed.add_field(name=title, value=value, inline=False)
    embed.set_footer(text=FOOTER)
    _fit_total(embed)
    return embed


def _command_line(info: CommandInfo, summary: str) -> str:
    line = f"`{usage_line(info)}`"
    return f"{line} · {summary}" if summary else line


def _fit_lines(lines: list[str], limit: int) -> str:
    """Join whole lines, dropping them from the bottom (+N more) until they fit."""
    for keep in range(len(lines), -1, -1):
        dropped = len(lines) - keep
        text = "\n".join(lines[:keep] + ([f"+{dropped} more"] if dropped else []))
        if len(text) <= limit:
            return text
    return ""


def _fit_total(embed: discord.Embed) -> None:
    """Drop fields from the end until the embed fits Discord's total size limit."""
    while len(embed) > EMBED_TOTAL_LIMIT and embed.fields:
        embed.remove_field(len(embed.fields) - 1)
