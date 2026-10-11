from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ninja_bot.help import content
from ninja_bot.help.content import COMMANDS, GROUPS, CommandHelp

MAX_SUMMARY = 100
MAX_DETAILS = 600
MAX_EXAMPLE = 150

EXPECTED_NAMES = {
    "ping",
    "download",
    "link",
    "unlink",
    "scoreboard",
    "profile",
    "leaderboard",
    "help",
}


def by_name(entry: CommandHelp) -> str:
    return entry.name


def test_names_are_unique_lowercase_without_slash() -> None:
    names = [entry.name for entry in COMMANDS]
    assert len(names) == len(set(names))
    for name in names:
        assert name
        assert name == name.lower()
        assert "/" not in name


def test_every_expected_command_has_an_entry() -> None:
    assert {entry.name for entry in COMMANDS} == EXPECTED_NAMES


def test_groups_are_unique_and_in_display_order() -> None:
    keys = [group.key for group in GROUPS]
    assert len(keys) == len(set(keys)) == 3
    assert all(group.title for group in GROUPS)


@pytest.mark.parametrize("entry", COMMANDS, ids=by_name)
def test_group_exists(entry: CommandHelp) -> None:
    assert entry.group in {group.key for group in GROUPS}


@pytest.mark.parametrize("entry", COMMANDS, ids=by_name)
def test_length_limits(entry: CommandHelp) -> None:
    assert entry.summary
    assert len(entry.summary) <= MAX_SUMMARY
    assert entry.details
    assert len(entry.details) <= MAX_DETAILS
    for example in entry.examples:
        assert len(example) <= MAX_EXAMPLE


@pytest.mark.parametrize("entry", COMMANDS, ids=by_name)
def test_examples_start_with_own_command(entry: CommandHelp) -> None:
    for example in entry.examples:
        assert example == f"/{entry.name}" or example.startswith(f"/{entry.name} ")


@pytest.mark.parametrize("entry", COMMANDS, ids=by_name)
def test_parameter_note_keys_are_non_empty(entry: CommandHelp) -> None:
    for key, note in entry.parameter_notes.items():
        assert key.strip()
        assert note.strip()


@pytest.mark.parametrize(
    "name",
    [
        "OVERVIEW_TITLE",
        "OVERVIEW_INTRO",
        "GETTING_STARTED_TITLE",
        "GETTING_STARTED",
        "USAGE_FIELD",
        "PARAMETERS_FIELD",
        "EXAMPLES_FIELD",
        "NOTES_FIELD",
        "FOOTER",
        "OTHER_GROUP_TITLE",
        "UNKNOWN_COMMAND_MESSAGE",
        "SUGGESTIONS_TEXT",
        "NO_SUGGESTIONS",
        "PUBLIC_COOLDOWN_MESSAGE",
        "GENERIC_ERROR_MESSAGE",
    ],
)
def test_text_constants_are_non_empty_strings(name: str) -> None:
    value = getattr(content, name)
    assert isinstance(value, str)
    assert value.strip()


def test_placeholders_are_present() -> None:
    assert "{suggestions}" in content.UNKNOWN_COMMAND_MESSAGE
    assert "{names}" in content.SUGGESTIONS_TEXT
    assert "{seconds}" in content.PUBLIC_COOLDOWN_MESSAGE
    assert content.PUBLIC_COOLDOWN_MESSAGE.format(seconds=3, unit="seconds")


def test_content_module_imports_no_discord_or_ninja_bot() -> None:
    tree = ast.parse(Path(content.__file__).read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    for module in modules:
        assert module.split(".")[0] not in {"discord", "ninja_bot"}, module
