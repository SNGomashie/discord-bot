from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import discord

from ninja_bot.activity.awards import Award, AwardKey, SocialStats
from ninja_bot.activity.embed import (
    AWARDS_FIELD,
    EMPTY_MONTH_MESSAGE,
    LEADERBOARD_CONTINUED_FIELD,
    LEADERBOARD_FIELD,
    MAX_LEADERBOARD_ROWS,
    SHAME_FIELD,
    TOGETHER_FIELD,
    build_monthly_embed,
    build_profile_embed,
    format_awards,
)
from ninja_bot.activity.periods import Month
from ninja_bot.activity.report import (
    SHAME_LIMIT,
    MonthlyReport,
    build_monthly_report,
    user_profile,
)
from ninja_bot.activity.store import Session
from ninja_bot.cs2.embed import FIELD_VALUE_LIMIT, TITLE_LIMIT

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
SEPTEMBER = Month(2026, 9)
HOUR = 3600.0
LATER = datetime(2026, 12, 1, tzinfo=UTC).timestamp()
LONG_NAME = "[evil](https://x.y) **bold** " + "W" * 200


def ts(day: int, hour: int, month: int = 9) -> float:
    return datetime(2026, month, day, hour, tzinfo=AMSTERDAM).timestamp()


_ids = iter(range(1, 10**9))


def session(user_id: int, start: float, hours: float, channel_id: int = 10) -> Session:
    return Session(
        id=next(_ids),
        user_id=user_id,
        channel_id=channel_id,
        start_ts=start,
        end_ts=start + hours * HOUR,
        last_seen=None,
    )


def report(
    sessions: list[Session],
    known: set[int] | None = None,
    previous: list[Session] | None = None,
) -> MonthlyReport:
    return build_monthly_report(
        sessions, previous or [], known or set(), SEPTEMBER, AMSTERDAM, LATER
    )


def busy_month(users: int) -> list[Session]:
    """`users` members with different totals, spread over many days and channels."""
    sessions: list[Session] = []
    for user_id in range(10**17, 10**17 + users):
        index = user_id - 10**17
        for day in range(1, 1 + (index % 28) + 1):
            sessions.append(session(user_id, ts(day, 20), 1 + index / 10, channel_id=index % 3))
    return sessions


def field(embed: discord.Embed, name: str) -> str:
    values = [f.value for f in embed.fields if f.name == name]
    assert len(values) == 1, [f.name for f in embed.fields]
    return str(values[0])


def assert_within_limits(embed: discord.Embed) -> None:
    assert len(embed) <= 6000
    assert len(embed.fields) <= 25
    assert len(embed.title or "") <= TITLE_LIMIT
    for embed_field in embed.fields:
        assert embed_field.value and str(embed_field.value).strip()
        assert len(str(embed_field.value)) <= FIELD_VALUE_LIMIT
        assert len(str(embed_field.name)) <= 256


def test_full_report_with_40_users_fits_discord_limits() -> None:
    sessions = busy_month(40)
    previous = [session(10**17, ts(3, 20, month=8), 50)]
    embed = build_monthly_embed(report(sessions, known={1, 2, 3}, previous=previous))

    assert_within_limits(embed)
    names = [f.name for f in embed.fields]
    assert names[0] == LEADERBOARD_FIELD
    assert LEADERBOARD_CONTINUED_FIELD in names
    assert {SHAME_FIELD, AWARDS_FIELD, TOGETHER_FIELD} <= set(names)
    rows = "\n".join(
        str(f.value) for f in embed.fields if f.name.startswith(LEADERBOARD_FIELD)
    ).splitlines()
    assert len(rows) == MAX_LEADERBOARD_ROWS
    assert embed.footer.text == "+15 more · Times in Europe/Amsterdam"


def test_title_carries_the_month() -> None:
    embed = build_monthly_embed(report([session(1, ts(2, 20), 2)]))
    assert embed.title == "Voice activity · September 2026"


def test_empty_month_shows_empty_state() -> None:
    embed = build_monthly_embed(report([], known={1, 2}))

    assert embed.description == EMPTY_MONTH_MESSAGE.format(month="September 2026")
    assert embed.fields == []
    assert embed.footer.text == "Times in Europe/Amsterdam"


def test_single_user() -> None:
    embed = build_monthly_embed(report([session(7, ts(2, 8), 12.5)]))

    assert field(embed, LEADERBOARD_FIELD) == "🥇 <@7> **12h 30m** · 1 day"
    assert field(embed, SHAME_FIELD) == "🐌 <@7> 12h 30m"
    assert "🔊 Server total: 12h 30m" in field(embed, TOGETHER_FIELD)
    assert embed.footer.text == "Times in Europe/Amsterdam"
    assert_within_limits(embed)


def test_ties_share_a_rank() -> None:
    sessions = [
        session(1, ts(2, 20), 3),
        session(2, ts(2, 20), 3, channel_id=11),
        session(3, ts(3, 20), 2),
        session(4, ts(3, 20), 1, channel_id=12),
    ]
    lines = field(build_monthly_embed(report(sessions)), LEADERBOARD_FIELD).splitlines()

    assert lines == [
        "🥇 <@1> **3h 00m** · 1 day",
        "🥇 <@2> **3h 00m** · 1 day",
        "🥉 <@3> **2h 00m** · 1 day",
        "4. <@4> **1h 00m** · 1 day",
    ]


def test_shame_includes_members_without_voice_time() -> None:
    sessions = [session(1, ts(2, 20), 3), session(2, ts(2, 20), 1, channel_id=11)]
    shame = field(build_monthly_embed(report(sessions, known={1, 2, 3, 4})), SHAME_FIELD)

    assert shame.splitlines() == ["🐌 <@3> 0m", "🐌 <@4> 0m", "🐌 <@2> 1h 00m"]
    assert len(shame.splitlines()) == SHAME_LIMIT


def test_together_lists_pair_and_busiest_times() -> None:
    sessions = [session(1, ts(4, 21), 2), session(2, ts(4, 21), 1)]  # Friday
    together = field(build_monthly_embed(report(sessions)), TOGETHER_FIELD)

    assert together.splitlines() == [
        "👥 <@1> + <@2>: 1h 00m together",
        "🕘 Busiest hour: 21:00–22:00",
        "📆 Busiest day: Friday",
        "🔊 Server total: 3h 00m",
    ]


def test_award_lines_group_ties_and_format_values() -> None:
    awards = [
        Award(AwardKey.NIGHT_OWL, 1, 2 * HOUR, "Night owl"),
        Award(AwardKey.NIGHT_OWL, 2, 2 * HOUR, "Night owl"),
        Award(AwardKey.MOST_CONSISTENT, 3, 12, "Most consistent"),
        Award(AwardKey.BIGGEST_CLIMBER, 4, 5 * HOUR, "Biggest climber"),
        Award(AwardKey.BIGGEST_DROP, 5, 90 * 60, "Biggest drop"),
    ]

    assert format_awards(awards).splitlines() == [
        "🦉 Night owl: <@1>, <@2> (2h 00m)",
        "📅 Most consistent: <@3> (12 active days)",
        "📈 Biggest climber: <@4> (+5h 00m)",
        "📉 Biggest drop: <@5> (-1h 30m)",
    ]
    assert format_awards([]) == ""


def test_no_field_has_an_empty_value() -> None:
    # One short session: no pair, no awards with value 0 are added as empty fields.
    embed = build_monthly_embed(report([session(1, ts(2, 20), 0.01)]))
    assert_within_limits(embed)


def test_profile_shows_the_members_month() -> None:
    sessions = [
        session(1, ts(1, 20), 2),
        session(1, ts(2, 20), 1),
        session(2, ts(2, 20), 5, channel_id=11),
    ]
    profile = user_profile(report(sessions), 1, "Ninja")
    assert profile is not None
    embed = build_profile_embed(profile)

    assert embed.title == "Ninja · September 2026"
    assert field(embed, "Voice time") == "**3h 00m**\nRank #2 of 2"
    assert field(embed, "Active days") == "2 of 30"
    assert field(embed, "Streaks") == "Current: 0 days\nLongest: 2 days"
    assert field(embed, "Sessions") == "2 sessions · longest 2h 00m · average 1h 30m"
    assert field(embed, "Days").startswith("🟩🟩⬜")
    assert embed.footer.text == "Times in Europe/Amsterdam"
    assert_within_limits(embed)


def test_profile_lists_awards_won_without_mentions() -> None:
    profile = user_profile(report([session(1, ts(2, 2), 3)]), 1, "Owl")
    assert profile is not None
    awards = field(build_profile_embed(profile), AWARDS_FIELD)

    assert "🦉 Night owl (3h 00m)" in awards.splitlines()
    assert "<@" not in awards


def test_profile_escapes_and_truncates_long_names() -> None:
    profile = user_profile(report(busy_month(40)), 10**17 + 39, LONG_NAME)
    assert profile is not None
    embed = build_profile_embed(profile)

    assert embed.title is not None
    assert "[evil](" not in embed.title
    assert "\\*\\*bold\\*\\*" in embed.title
    assert_within_limits(embed)


def test_profile_without_data_is_none() -> None:
    assert user_profile(report([session(1, ts(2, 20), 1)]), 2, "Nobody") is None


def test_social_stats_without_pair_omit_the_pair_line() -> None:
    social = SocialStats(
        top_pair=None, busiest_hour=None, busiest_weekday=None, total_voice_seconds=0.0
    )
    report_value = report([session(1, ts(2, 20), 1)])
    embed = build_monthly_embed(MonthlyReport(**{**report_value.__dict__, "social": social}))
    assert field(embed, TOGETHER_FIELD) == "🔊 Server total: 0m"
