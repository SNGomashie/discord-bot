"""Discord embeds for the monthly voice overview and a member's profile."""

from __future__ import annotations

from collections.abc import Sequence

import discord

from ninja_bot.activity.awards import Award, AwardKey, SocialStats
from ninja_bot.activity.formatting import (
    WEEKDAY_NAMES,
    day_strip,
    format_hours,
    hour_range,
    month_label,
    plural,
)
from ninja_bot.activity.pokemon_stats import PokemonStats, PokemonUserStats
from ninja_bot.activity.report import MonthlyReport, UserProfile
from ninja_bot.activity.stats import RankedRow, rank
from ninja_bot.cs2.embed import FIELD_NAME_LIMIT, FIELD_VALUE_LIMIT, TITLE_LIMIT, pack_lines
from ninja_bot.cs2.formatting import escape_name

MONTHLY_COLOUR = discord.Colour.teal()
PROFILE_COLOUR = discord.Colour.blurple()
LEADERBOARD_FIELD = "Leaderboard"
LEADERBOARD_CONTINUED_FIELD = "Leaderboard (cont.)"
SHAME_FIELD = "Wall of shame"
AWARDS_FIELD = "Awards"
TOGETHER_FIELD = "Together"
TOTAL_FIELD = "Voice time"
ACTIVE_DAYS_FIELD = "Active days"
STREAKS_FIELD = "Streaks"
SESSIONS_FIELD = "Sessions"
DAYS_FIELD = "Days"
POKEMON_FIELD = "Pokémon"
MAX_LEADERBOARD_ROWS = 25
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
SHAME_ICON = "🐌"
AWARD_ICONS = {
    AwardKey.NIGHT_OWL: "🦉",
    AwardKey.MARATHON: "🏃",
    AwardKey.MOST_CONSISTENT: "📅",
    AwardKey.BIGGEST_CLIMBER: "📈",
    AwardKey.BIGGEST_DROP: "📉",
    AwardKey.EARLY_BIRD: "🐦",
    AwardKey.IMPATIENT: "⏳",
}
# Awards shown in the Pokémon field instead of the Awards field.
POKEMON_AWARDS = frozenset({AwardKey.IMPATIENT})
MAX_POKEMON_CATCHERS = 5
MAX_SHINY_LINES = 5
MAX_TOP_SPECIES = 5
SHINY_ICON = "✨"
TOP_SPECIES_ICON = "🏆"
POKEMON_TOTAL_ICON = "🎯"
EMPTY_MONTH_MESSAGE = "Nobody was in voice in {month}."
TIMEZONE_FOOTER = "Times in {timezone}"
FOOTER_SEPARATOR = " · "


def build_monthly_embed(report: MonthlyReport) -> discord.Embed:
    """Leaderboard, wall of shame, awards, server stats and Pokémon for one month.

    At most `MAX_LEADERBOARD_ROWS` members are listed (`+N more` in the footer); the
    leaderboard is split over several fields without cutting a line. Mentions are built
    only from stored numeric Discord IDs; send with `AllowedMentions.none()`.
    """
    label = month_label(report.month.year, report.month.month)
    embed = discord.Embed(title=_truncate(f"Voice activity · {label}", TITLE_LIMIT))
    embed.colour = MONTHLY_COLOUR
    footer = [TIMEZONE_FOOTER.format(timezone=report.timezone)]
    if not report.leaderboard:
        embed.description = EMPTY_MONTH_MESSAGE.format(month=label)
        _add_field(embed, POKEMON_FIELD, format_pokemon(report))
        embed.set_footer(text=footer[0])
        return embed

    lines = [format_leaderboard_row(row, report) for row in report.leaderboard]
    for index, chunk in enumerate(pack_lines(lines[:MAX_LEADERBOARD_ROWS], FIELD_VALUE_LIMIT)):
        name = LEADERBOARD_FIELD if index == 0 else LEADERBOARD_CONTINUED_FIELD
        _add_field(embed, name, chunk)
    _add_field(embed, SHAME_FIELD, format_shame(report.shame))
    voice_awards = [award for award in report.awards if award.key not in POKEMON_AWARDS]
    _add_field(embed, AWARDS_FIELD, format_awards(voice_awards))
    _add_field(embed, TOGETHER_FIELD, format_together(report.social))
    _add_field(embed, POKEMON_FIELD, format_pokemon(report))
    hidden = len(report.leaderboard) - MAX_LEADERBOARD_ROWS
    if hidden > 0:
        footer.insert(0, f"+{hidden} more")
    embed.set_footer(text=FOOTER_SEPARATOR.join(footer))
    return embed


def build_profile_embed(profile: UserProfile) -> discord.Embed:
    """One member's month: time and rank, active days, streaks, sessions, day strip, awards
    and (when the month has Pokémon data) their catches."""
    label = month_label(profile.month.year, profile.month.month)
    stats = profile.stats
    embed = discord.Embed(
        title=_truncate(f"{escape_name(profile.display_name)} · {label}", TITLE_LIMIT),
        colour=PROFILE_COLOUR,
    )
    _add_field(
        embed,
        TOTAL_FIELD,
        f"**{format_hours(stats.total_seconds)}**\nRank #{profile.rank} of {profile.ranked_users}",
        inline=True,
    )
    _add_field(
        embed,
        ACTIVE_DAYS_FIELD,
        f"{len(stats.active_days)} of {profile.days_in_month}",
        inline=True,
    )
    _add_field(
        embed,
        STREAKS_FIELD,
        f"Current: {plural(stats.current_streak, 'day')}\n"
        f"Longest: {plural(stats.longest_streak, 'day')}",
        inline=True,
    )
    _add_field(
        embed,
        SESSIONS_FIELD,
        f"{plural(stats.sessions_count, 'session')}"
        f" · longest {format_hours(stats.longest_session_seconds)}"
        f" · average {format_hours(stats.average_session_seconds)}",
        inline=False,
    )
    _add_field(
        embed,
        DAYS_FIELD,
        day_strip(set(stats.active_days), profile.days_in_month),
        inline=False,
    )
    _add_field(embed, AWARDS_FIELD, format_awards(profile.awards, mentions=False), inline=False)
    if profile.pokemon is not None:
        _add_field(embed, POKEMON_FIELD, format_pokemon_profile(profile.pokemon), inline=False)
    embed.set_footer(text=TIMEZONE_FOOTER.format(timezone=profile.timezone))
    return embed


def format_leaderboard_row(row: RankedRow, report: MonthlyReport) -> str:
    """`🥇 <@id> **12h 30m** · 18 days`; ranks past 3 show `4.`."""
    position = MEDALS.get(row.rank, f"{row.rank}.")
    user_stats = report.stats.get(row.user_id)
    days = len(user_stats.active_days) if user_stats is not None else 0
    return f"{position} <@{row.user_id}> **{format_hours(row.value)}** · {plural(days, 'day')}"


def format_shame(shame: Sequence[tuple[int, float]]) -> str:
    """`🐌 <@id> 0m`, lowest first."""
    return "\n".join(
        f"{SHAME_ICON} <@{user_id}> {format_hours(seconds)}" for user_id, seconds in shame
    )


def format_awards(awards: Sequence[Award], mentions: bool = True) -> str:
    """One line per award; tied winners share the line: `🦉 Night owl: <@1>, <@2> (2h 05m)`."""
    by_key: dict[AwardKey, list[Award]] = {}
    for award in awards:
        by_key.setdefault(award.key, []).append(award)
    lines: list[str] = []
    for key, winners in by_key.items():
        first = winners[0]
        line = f"{AWARD_ICONS.get(key, '🏅')} {first.label}"
        if mentions:
            line += ": " + ", ".join(f"<@{award.user_id}>" for award in winners)
        lines.append(f"{line} ({_award_value(first)})")
    return _fit_lines(lines)


def format_together(social: SocialStats) -> str:
    """Top pair, busiest hour and weekday, and the server's total voice time."""
    lines: list[str] = []
    pair = social.top_pair
    if pair is not None:
        lines.append(
            f"👥 <@{pair.first}> + <@{pair.second}>: {format_hours(pair.seconds)} together"
        )
    if social.busiest_hour is not None:
        lines.append(f"🕘 Busiest hour: {hour_range(social.busiest_hour)}")
    if social.busiest_weekday is not None:
        lines.append(f"📆 Busiest day: {WEEKDAY_NAMES[social.busiest_weekday]}")
    lines.append(f"🔊 Server total: {format_hours(social.total_voice_seconds)}")
    return _fit_lines(lines)


def format_pokemon(report: MonthlyReport) -> str:
    """Top catchers (shared ranks), shinies, most caught species, the Impatient award and
    the server's catches and cooldown hits."""
    pokemon = report.pokemon
    if not pokemon.has_data:
        return ""
    lines = format_top_catchers(pokemon)
    lines.extend(format_shinies(pokemon))
    if pokemon.top_species:
        lines.append(format_top_species(pokemon))
    impatient = [award for award in report.awards if award.key in POKEMON_AWARDS]
    if impatient:
        lines.append(format_awards(impatient))
    users = pokemon.users.values()
    catches = sum(user.catches for user in users)
    hits = sum(user.cooldown_hits for user in users)
    lines.append(
        f"{POKEMON_TOTAL_ICON} Server: {_catches(catches)} · {plural(hits, 'cooldown hit')}"
    )
    return _fit_lines(lines)


def format_top_catchers(pokemon: PokemonStats) -> list[str]:
    """`🥇 <@id> **12 catches**`, at most `MAX_POKEMON_CATCHERS` lines plus `+N more`."""
    catches = [(user_id, float(user.catches)) for user_id, user in pokemon.users.items()]
    rows = rank([(user_id, value) for user_id, value in catches if value > 0])
    lines = [
        f"{MEDALS.get(row.rank, f'{row.rank}.')} <@{row.user_id}> **{_catches(int(row.value))}**"
        for row in rows[:MAX_POKEMON_CATCHERS]
    ]
    if len(rows) > MAX_POKEMON_CATCHERS:
        lines.append(f"+{len(rows) - MAX_POKEMON_CATCHERS} more")
    return lines


def format_shinies(pokemon: PokemonStats) -> list[str]:
    """`✨ <@id>: shiny Pikachu`, oldest first, at most `MAX_SHINY_LINES` plus `+N more`."""
    lines = [
        f"{SHINY_ICON} <@{shiny.user_id}>: shiny {escape_name(shiny.pokemon)}"
        for shiny in pokemon.shinies[:MAX_SHINY_LINES]
    ]
    hidden = len(pokemon.shinies) - MAX_SHINY_LINES
    if hidden > 0:
        lines.append(f"{SHINY_ICON} +{hidden} more")
    return lines


def format_top_species(pokemon: PokemonStats) -> str:
    """`🏆 Most caught: Eevee, Pikachu (3 each)`; at most `MAX_TOP_SPECIES` names."""
    names = [escape_name(name) for name in pokemon.top_species[:MAX_TOP_SPECIES]]
    hidden = len(pokemon.top_species) - MAX_TOP_SPECIES
    if hidden > 0:
        names.append(f"+{hidden} more")
    count = pokemon.top_species_count
    suffix = f"{count} each" if len(pokemon.top_species) > 1 else str(count)
    return f"{TOP_SPECIES_ICON} Most caught: {', '.join(names)} ({suffix})"


def format_pokemon_profile(user: PokemonUserStats) -> str:
    """`12 catches · 1 shiny · 4 cooldown hits`."""
    return (
        f"{_catches(user.catches)} · {_shinies(user.shinies)}"
        f" · {plural(user.cooldown_hits, 'cooldown hit')}"
    )


def _catches(count: int) -> str:
    return f"{count} catch" if count == 1 else f"{count} catches"


def _shinies(count: int) -> str:
    return f"{count} shiny" if count == 1 else f"{count} shinies"


def _award_value(award: Award) -> str:
    if award.key is AwardKey.IMPATIENT:
        return plural(int(award.value), "cooldown hit")
    if award.key is AwardKey.MOST_CONSISTENT:
        return plural(int(award.value), "active day")
    if award.key is AwardKey.BIGGEST_CLIMBER:
        return f"+{format_hours(award.value)}"
    if award.key is AwardKey.BIGGEST_DROP:
        return f"-{format_hours(award.value)}"
    return format_hours(award.value)


def _fit_lines(lines: Sequence[str]) -> str:
    """The first chunk of whole lines that fits a field value."""
    chunks = pack_lines(lines, FIELD_VALUE_LIMIT)
    return chunks[0] if chunks else ""


def _add_field(embed: discord.Embed, name: str, value: str, inline: bool = False) -> None:
    """Add a field, skipping it when there is nothing to show."""
    if not value.strip():
        return
    embed.add_field(
        name=_truncate(name, FIELD_NAME_LIMIT),
        value=_truncate(value, FIELD_VALUE_LIMIT),
        inline=inline,
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
