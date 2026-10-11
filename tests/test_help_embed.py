from __future__ import annotations

import discord
import pytest

from ninja_bot.help import embed as help_embed
from ninja_bot.help.commands import CommandInfo, ParamInfo, usage_line
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
from ninja_bot.help.embed import (
    EMBED_TOTAL_LIMIT,
    FIELD_VALUE_LIMIT,
    MAX_FIELDS,
    build_overview_embed,
)

HELP_BY_NAME = {entry.name: entry for entry in COMMANDS}
GROUP_TITLES = {group.key: group.title for group in GROUPS}


def info_for(name: str, description: str = "Discord description") -> CommandInfo:
    """A registered command with one param per documented parameter note."""
    entry = HELP_BY_NAME.get(name)
    params = tuple(
        ParamInfo(param, f"The {param} parameter of /{name}", required=index == 0)
        for index, param in enumerate(entry.parameter_notes if entry else ())
    )
    return CommandInfo(name, description, params)


def all_commands() -> list[CommandInfo]:
    return [info_for(entry.name) for entry in COMMANDS]


def field_values(embed: discord.Embed) -> dict[str, str]:
    return {field.name or "": field.value or "" for field in embed.fields}


def test_overview_text_comes_from_content() -> None:
    embed = build_overview_embed(all_commands())
    assert embed.title == OVERVIEW_TITLE
    assert embed.description == OVERVIEW_INTRO
    assert embed.footer.text == FOOTER
    assert embed.fields[0].name == GETTING_STARTED_TITLE
    assert embed.fields[0].value == GETTING_STARTED


def test_all_groups_present_in_order() -> None:
    embed = build_overview_embed(all_commands())
    names = [field.name for field in embed.fields[1:]]
    assert names == [group.title for group in GROUPS]


def test_group_lines_follow_commands_order_with_usage_and_summary() -> None:
    commands = all_commands()
    embed = build_overview_embed(list(reversed(commands)))
    values = field_values(embed)
    by_name = {info.name: info for info in commands}
    for group in GROUPS:
        expected = [
            f"`{usage_line(by_name[entry.name])}` · {entry.summary}"
            for entry in COMMANDS
            if entry.group == group.key
        ]
        assert values[group.title].split("\n") == expected


def test_empty_group_is_omitted() -> None:
    cs2_names = {entry.name for entry in COMMANDS if entry.group == "cs2"}
    commands = [info for info in all_commands() if info.name not in cs2_names]
    embed = build_overview_embed(commands)
    names = [field.name for field in embed.fields]
    assert GROUP_TITLES["cs2"] not in names
    assert GROUP_TITLES["media"] in names
    assert GROUP_TITLES["utility"] in names


def test_unregistered_command_is_skipped() -> None:
    commands = [info for info in all_commands() if info.name != "download"]
    embed = build_overview_embed(commands)
    text = "\n".join(field.value or "" for field in embed.fields[1:])
    assert "`/download" not in text
    assert GROUP_TITLES["media"] not in [field.name for field in embed.fields]


def test_undocumented_command_goes_under_other() -> None:
    extra = CommandInfo("roll", "Roll a dice", (ParamInfo("sides", "Sides", required=False),))
    embed = build_overview_embed([*all_commands(), extra])
    assert embed.fields[-1].name == OTHER_GROUP_TITLE
    assert embed.fields[-1].value == "`/roll [sides:<sides>]` · Roll a dice"


def test_no_other_group_when_everything_is_documented() -> None:
    embed = build_overview_embed(all_commands())
    assert OTHER_GROUP_TITLE not in [field.name for field in embed.fields]


def test_long_group_is_truncated_with_more_line() -> None:
    extras = [CommandInfo(f"cmd{index}", "x" * 100) for index in range(30)]
    embed = build_overview_embed(extras)
    other = field_values(embed)[OTHER_GROUP_TITLE]
    assert len(other) <= FIELD_VALUE_LIMIT
    lines = other.split("\n")
    kept = len(lines) - 1
    assert lines[-1] == f"+{30 - kept} more"
    assert lines[:kept] == [f"`/cmd{index}` · {'x' * 100}" for index in range(kept)]


def test_total_limit_drops_trailing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(help_embed, "EMBED_TOTAL_LIMIT", 600)
    embed = build_overview_embed(all_commands())
    assert len(embed) <= 600
    assert embed.fields[0].name == GETTING_STARTED_TITLE


def test_full_real_content_fits_discord_limits() -> None:
    embed = build_overview_embed(all_commands())
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert len(embed.fields) <= MAX_FIELDS
    for field in embed.fields:
        assert len(field.value or "") <= FIELD_VALUE_LIMIT
    text = "\n".join(field.value or "" for field in embed.fields)
    for entry in COMMANDS:
        assert f"`/{entry.name}" in text
