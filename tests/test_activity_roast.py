import dataclasses
import random
from zoneinfo import ZoneInfo

from ninja_bot.activity.content import INTRO_LINES, ROAST_FIRST, ROAST_SECOND, ROAST_THIRD
from ninja_bot.activity.periods import Month
from ninja_bot.activity.report import MonthlyReport, build_monthly_report
from ninja_bot.activity.roast import monthly_message
from ninja_bot.activity.stats import RankedRow
from ninja_bot.flavor import LinePicker


def report(*rows: RankedRow) -> MonthlyReport:
    empty = build_monthly_report([], [], {}, Month(2026, 9), ZoneInfo("UTC"), 0.0)
    return dataclasses.replace(empty, leaderboard=list(rows))


def picker() -> LinePicker:
    return LinePicker(random.Random(3))


def test_empty_month_is_intro_only() -> None:
    text = monthly_message(report(), picker())
    assert "overview of the month" in text.lower()
    assert "\n" not in text


def test_top_three_are_roasted_with_their_time() -> None:
    rows = [
        RankedRow(1, 11, 3600 * 100 + 7 * 60),
        RankedRow(2, 22, 3600 * 50),
        RankedRow(3, 33, 3600 * 5),
        RankedRow(4, 44, 3600),
    ]
    lines = monthly_message(report(*rows), picker()).split("\n")
    assert len(lines) == 4
    assert "<@11>" in lines[1] and "100h 07m" in lines[1] and lines[1].startswith("🥇")
    assert "<@22>" in lines[2] and "50h 00m" in lines[2]
    assert "<@33>" in lines[3] and "5h 00m" in lines[3]
    assert "<@44>" not in "\n".join(lines)


def test_two_people_only_roasts_two() -> None:
    lines = monthly_message(report(RankedRow(1, 1, 7200), RankedRow(2, 2, 60)), picker()).split(
        "\n"
    )
    assert len(lines) == 3


def test_ties_roast_everyone_tied() -> None:
    rows = [RankedRow(1, 1, 7200), RankedRow(1, 2, 7200), RankedRow(3, 3, 60), RankedRow(4, 4, 1)]
    lines = monthly_message(report(*rows), picker()).split("\n")
    assert len(lines) == 4
    assert lines[1].startswith("🥇") and lines[2].startswith("🥇") and lines[3].startswith("🥉")


def test_message_varies_between_calls() -> None:
    p = picker()
    rep = report(RankedRow(1, 1, 7200))
    assert len({monthly_message(rep, p) for _ in range(20)}) > 5


def test_pools_are_big_unique_and_format() -> None:
    for pool in (INTRO_LINES,):
        assert len(set(pool)) == len(pool) >= 6
        for line in pool:
            assert "overview of the month" in line.lower()
    for pool in (ROAST_FIRST, ROAST_SECOND, ROAST_THIRD):
        assert len(set(pool)) == len(pool) >= 8
        for line in pool:
            assert "{user}" in line and "{hours}" in line
            line.format(user="<@1>", hours="1h 00m")


def test_stays_under_discord_limit() -> None:
    rows = [RankedRow(1, 10**18, 10**9)] * 3
    assert len(monthly_message(report(*rows), picker())) <= 2000
