"""Discord embeds for /help. All text comes from `help/content.py`."""

from __future__ import annotations

from collections.abc import Sequence

import discord

from ninja_bot.help.commands import CommandInfo, parameter_lines, usage_line
from ninja_bot.help.content import (
    COMMANDS,
    EXAMPLES_FIELD,
    FOOTER,
    GETTING_STARTED,
    GETTING_STARTED_TITLE,
    GROUPS,
    NOTES_FIELD,
    OTHER_GROUP_TITLE,
    OVERVIEW_INTRO,
    OVERVIEW_TITLE,
    PARAMETERS_FIELD,
    USAGE_FIELD,
    CommandHelp,
)

# Discord embed limits.
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
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


def build_command_embed(info: CommandInfo, help_entry: CommandHelp | None) -> discord.Embed:
    """Full help for one registered command: usage, parameters, examples and notes."""
    if help_entry is None:
        description = info.description
    else:
        description = "\n\n".join(part for part in (help_entry.summary, help_entry.details) if part)
    embed = discord.Embed(
        title=_cut(f"/{info.name}", TITLE_LIMIT),
        description=_cut(description, DESCRIPTION_LIMIT) or None,
        colour=HELP_COLOUR,
    )
    notes = help_entry.parameter_notes if help_entry else {}
    examples = list(help_entry.examples) if help_entry else []
    bullets = [f"• {note}" for note in help_entry.notes] if help_entry else []
    fields = [
        (USAGE_FIELD, _code_block([_cut(usage_line(info), FIELD_VALUE_LIMIT - 8)])),
        (PARAMETERS_FIELD, _fit_lines(parameter_lines(info, notes), FIELD_VALUE_LIMIT)),
        (EXAMPLES_FIELD, _fit_code_block(examples, FIELD_VALUE_LIMIT)),
        (NOTES_FIELD, _fit_lines(bullets, FIELD_VALUE_LIMIT)),
    ]
    for name, value in fields:
        if value:
            embed.add_field(name=name, value=value, inline=False)
    _fit_total(embed)
    return embed


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _code_block(lines: list[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```" if lines else ""


def _fit_code_block(lines: list[str], limit: int) -> str:
    """Whole lines in one code block, dropping them from the bottom (+N more) until it fits."""
    for keep in range(len(lines), -1, -1):
        dropped = len(lines) - keep
        parts = [_code_block(lines[:keep])] if keep else []
        if dropped:
            parts.append(f"+{dropped} more")
        text = "\n".join(parts)
        if len(text) <= limit:
            return text
    return ""


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
