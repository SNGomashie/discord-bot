from __future__ import annotations

import re

import discord
import pytest

from ninja_bot.help import embed as help_embed
from ninja_bot.help.commands import CommandInfo, ParamInfo, parameter_lines, usage_line
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
from ninja_bot.help.embed import (
    EMBED_TOTAL_LIMIT,
    FIELD_VALUE_LIMIT,
    MAX_FIELDS,
    TITLE_LIMIT,
    build_command_embed,
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


def test_command_embed_with_parameters_choices_and_notes() -> None:
    info = CommandInfo(
        "download",
        "Download a video",
        (
            ParamInfo("url", "Video link", required=True),
            ParamInfo("format", "File type", required=False, choices=("mp4", "mp3")),
        ),
    )
    entry = CommandHelp(
        name="download",
        group="media",
        summary="Short summary.",
        details="Longer details.",
        examples=("/download url:a", "/download url:b format:mp3"),
        notes=("First note.", "Second note."),
        parameter_notes={"url": "Must be http."},
    )
    embed = build_command_embed(info, entry)
    assert embed.title == "/download"
    assert embed.description == "Short summary.\n\nLonger details."
    assert [field.name for field in embed.fields] == [
        USAGE_FIELD,
        PARAMETERS_FIELD,
        EXAMPLES_FIELD,
        NOTES_FIELD,
    ]
    values = field_values(embed)
    assert values[USAGE_FIELD] == f"```\n{usage_line(info)}\n```"
    assert values[PARAMETERS_FIELD].split("\n") == parameter_lines(info, entry.parameter_notes)
    assert "Choices: mp4, mp3" in values[PARAMETERS_FIELD]
    assert values[EXAMPLES_FIELD] == "```\n/download url:a\n/download url:b format:mp3\n```"
    assert values[NOTES_FIELD] == "• First note.\n• Second note."


def test_command_embed_without_parameters_has_no_parameters_field() -> None:
    info = CommandInfo("ping", "Check the bot")
    entry = CommandHelp(name="ping", group="utility", summary="Pong.", details="")
    embed = build_command_embed(info, entry)
    assert embed.description == "Pong."
    assert [field.name for field in embed.fields] == [USAGE_FIELD]
    assert field_values(embed)[USAGE_FIELD] == "```\n/ping\n```"


def test_command_embed_without_help_entry_uses_discord_description() -> None:
    info = CommandInfo("roll", "Roll a dice", (ParamInfo("sides", "Sides", required=False),))
    embed = build_command_embed(info, None)
    assert embed.title == "/roll"
    assert embed.description == "Roll a dice"
    assert [field.name for field in embed.fields] == [USAGE_FIELD, PARAMETERS_FIELD]
    assert field_values(embed)[PARAMETERS_FIELD] == "sides (optional): Sides"


def test_command_embed_trims_oversized_notes() -> None:
    notes = tuple(f"Note {index} " + "x" * 90 for index in range(30))
    entry = CommandHelp(name="roll", group="utility", summary="s", details="d", notes=notes)
    embed = build_command_embed(CommandInfo("roll", "Roll"), entry)
    value = field_values(embed)[NOTES_FIELD]
    assert len(value) <= FIELD_VALUE_LIMIT
    lines = value.split("\n")
    kept = len(lines) - 1
    assert 0 < kept < 30
    assert lines[-1] == f"+{30 - kept} more"
    assert lines[:kept] == [f"• {note}" for note in notes[:kept]]


def test_command_embed_trims_oversized_parameters_and_title() -> None:
    params = tuple(ParamInfo(f"p{index}", "y" * 200, required=False) for index in range(20))
    info = CommandInfo("z" * 300, "d", params)
    embed = build_command_embed(info, None)
    assert len(embed.title or "") <= TITLE_LIMIT
    for field in embed.fields:
        assert len(field.value or "") <= FIELD_VALUE_LIMIT
    assert field_values(embed)[PARAMETERS_FIELD].split("\n")[-1].endswith("more")
    assert len(embed) <= EMBED_TOTAL_LIMIT


def test_total_limit_drops_trailing_command_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(help_embed, "EMBED_TOTAL_LIMIT", 700)
    embed = build_command_embed(info_for("download"), HELP_BY_NAME["download"])
    assert len(embed) <= 700
    assert embed.fields[0].name == USAGE_FIELD


@pytest.mark.parametrize("entry", COMMANDS, ids=lambda entry: entry.name)
def test_real_command_embeds_fit_discord_limits(entry: CommandHelp) -> None:
    embed = build_command_embed(info_for(entry.name), entry)
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert len(embed.title or "") <= TITLE_LIMIT
    for field in embed.fields:
        assert len(field.value or "") <= FIELD_VALUE_LIMIT
        assert not re.fullmatch(r"\+\d+ more", (field.value or "").split("\n")[-1])
    names = [field.name for field in embed.fields]
    assert names[0] == USAGE_FIELD
    if entry.examples:
        assert EXAMPLES_FIELD in names
    if entry.notes:
        assert NOTES_FIELD in names
