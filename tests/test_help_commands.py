from __future__ import annotations

from ninja_bot.help.commands import (
    MAX_PARAMETER_LINE,
    CommandInfo,
    ParamInfo,
    parameter_lines,
    usage_line,
)

URL = ParamInfo("url", "Video link", required=True)
FORMAT = ParamInfo("format", "File type", required=False, choices=("mp3", "mp4"))
START = ParamInfo("start", "Clip start", required=False)


def test_usage_line_without_parameters() -> None:
    assert usage_line(CommandInfo("ping", "Check the bot")) == "/ping"


def test_usage_line_one_required() -> None:
    assert usage_line(CommandInfo("download", "d", (URL,))) == "/download url:<url>"


def test_usage_line_mixed_and_choices_in_order() -> None:
    info = CommandInfo("download", "d", (URL, FORMAT, START))
    assert usage_line(info) == "/download url:<url> [format:mp3|mp4] [start:<start>]"


def test_usage_line_required_choice() -> None:
    metric = ParamInfo("metric", "Metric", required=True, choices=("rating", "aim"))
    info = CommandInfo("leaderboard", "l", (metric,))
    assert usage_line(info) == "/leaderboard metric:rating|aim"


def test_parameter_lines_without_parameters() -> None:
    assert parameter_lines(CommandInfo("ping", "p"), {}) == []


def test_parameter_lines_required_optional_and_order() -> None:
    lines = parameter_lines(CommandInfo("download", "d", (URL, START)), {})
    assert lines == ["url (required): Video link", "start (optional): Clip start"]


def test_parameter_lines_choices() -> None:
    lines = parameter_lines(CommandInfo("download", "d", (FORMAT,)), {})
    assert lines == ["format (optional): File type Choices: mp3, mp4"]


def test_parameter_lines_note_present_and_absent() -> None:
    info = CommandInfo("download", "d", (URL, START))
    lines = parameter_lines(info, {"start": "Like 90 or 1:30.", "other": "unused"})
    assert lines == ["url (required): Video link", "start (optional): Clip start Like 90 or 1:30."]


def test_parameter_lines_cuts_long_note() -> None:
    lines = parameter_lines(CommandInfo("download", "d", (URL,)), {"url": "x" * 1000})
    assert len(lines) == 1
    assert len(lines[0]) == MAX_PARAMETER_LINE
    assert lines[0].startswith("url (required): Video link x")
    assert lines[0].endswith("…")


def test_functions_are_deterministic() -> None:
    info = CommandInfo("download", "d", (URL, FORMAT, START))
    notes = {"start": "note"}
    assert usage_line(info) == usage_line(info)
    assert parameter_lines(info, notes) == parameter_lines(info, notes)
