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
    POKEMON_FIELD,
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
    UserProfile,
    build_monthly_report,
    user_profile,
)
from ninja_bot.activity.store import PokemonEvent, PokemonKind, Session
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


def pokemon_event(
    user_id: int, when: float, pokemon: str | None = None, shiny: bool = False
) -> PokemonEvent:
    kind: PokemonKind = "cooldown" if pokemon is None else "catch"
    return PokemonEvent(next(_ids), user_id, when, kind, pokemon, shiny)


def pokemon_report(
    sessions: list[Session], events: list[PokemonEvent], known: set[int] | None = None
) -> MonthlyReport:
    return build_monthly_report(sessions, [], known or set(), SEPTEMBER, AMSTERDAM, LATER, events)


def test_no_pokemon_field_without_pokemon_data() -> None:
    embed = build_monthly_embed(report([session(1, ts(2, 20), 2)]))
    assert POKEMON_FIELD not in [f.name for f in embed.fields]
    profile = user_profile(report([session(1, ts(2, 20), 2)]), 1, "Ash")
    assert profile is not None
    assert POKEMON_FIELD not in [f.name for f in build_profile_embed(profile).fields]


def test_pokemon_field_ranks_catchers_and_lists_shinies() -> None:
    events = [
        pokemon_event(1, ts(1, 12), "Pikachu"),
        pokemon_event(1, ts(2, 12), "Eevee", shiny=True),
        pokemon_event(2, ts(3, 12), "Pikachu"),
        pokemon_event(2, ts(4, 12), "Eevee"),
        pokemon_event(3, ts(5, 12), "Mew"),
    ] + [pokemon_event(4, ts(day, 12)) for day in range(1, 4)]
    embed = build_monthly_embed(pokemon_report([session(1, ts(2, 20), 2)], events))
    value = field(embed, POKEMON_FIELD)
    assert value.splitlines() == [
        "🥇 <@1> **2 catches**",
        "🥇 <@2> **2 catches**",
        "🥉 <@3> **1 catch**",
        "✨ <@1>: shiny Eevee",
        "🏆 Most caught: Eevee, Pikachu (2 each)",
        "⏳ Impatient: <@4> (3 cooldown hits)",
        "🎯 Server: 5 catches · 3 cooldown hits",
    ]
    # Impatient lives in the Pokémon field, not in the voice awards.
    assert "Impatient" not in field(embed, AWARDS_FIELD)


def test_pokemon_field_in_a_month_without_voice() -> None:
    events = [pokemon_event(1, ts(1, 12)), pokemon_event(1, ts(2, 12))]
    embed = build_monthly_embed(pokemon_report([], events))
    assert embed.description == EMPTY_MONTH_MESSAGE.format(month="September 2026")
    # Only cooldowns (below the Impatient minimum): just the server line.
    assert field(embed, POKEMON_FIELD) == "🎯 Server: 0 catches · 2 cooldown hits"


def test_profile_shows_the_members_pokemon() -> None:
    events = [
        pokemon_event(1, ts(1, 12), "Pikachu", shiny=True),
        pokemon_event(1, ts(2, 12), "Eevee"),
    ] + [pokemon_event(1, ts(day, 13)) for day in range(1, 4)]
    data = pokemon_report([session(1, ts(2, 20), 2), session(2, ts(3, 20), 1)], events)
    embed = build_profile_embed(user_profile(data, 1, "Ash") or _fail())
    assert field(embed, POKEMON_FIELD) == "2 catches · 1 shiny · 3 cooldown hits"
    assert "⏳ Impatient (3 cooldown hits)" in field(embed, AWARDS_FIELD)
    # The month has Pokémon data, so members without catches see zeros.
    other = build_profile_embed(user_profile(data, 2, "Misty") or _fail())
    assert field(other, POKEMON_FIELD) == "0 catches · 0 shinies · 0 cooldown hits"


def test_pokemon_with_40_users_and_long_names_fits_discord_limits() -> None:
    long_species = "Pika" + "W" * 300
    users = range(10**17, 10**17 + 40)
    events = [
        pokemon_event(user_id, ts(1 + index % 28, 12), f"{long_species}{index % 9}", True)
        for index, user_id in enumerate(users)
    ] + [pokemon_event(user_id, ts(2, 13)) for user_id in users for _ in range(3)]
    data = pokemon_report(busy_month(40), events, known={1, 2, 3})
    embed = build_monthly_embed(data)
    assert_within_limits(embed)
    value = field(embed, POKEMON_FIELD)
    assert "✨ +35 more" in value
    profile = user_profile(data, 10**17, LONG_NAME)
    assert profile is not None
    assert_within_limits(build_profile_embed(profile))


def _fail() -> UserProfile:
    raise AssertionError("expected a profile")
