from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord

from ninja_bot.cs2.awards import Award
from ninja_bot.cs2.formatting import (
    escape_name,
    form_strip,
    format_rating,
    format_team_table,
    rating_bar,
)
from ninja_bot.cs2.identities import Identity
from ninja_bot.cs2.models import Match, MatchOutcome, PlayerMatchStats, Profile, Ranks
from ninja_bot.cs2.squad import (
    LeaderboardRow,
    Metric,
    format_metric_value,
    format_trend,
    metric_label,
)

# Discord embed limits.
TITLE_LIMIT = 256
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024

UNKNOWN_MAP = "Unknown map"
TEAM_NAMES = ("Team A", "Team B")
YOUR_TEAM = "Your team"
OTHER_PLAYERS_FIELD = "Other players"
NO_TEAM = "No team"
FRIENDS_FIELD = "Friends in this match"
MAX_FRIEND_LINES = 10
AWARDS_FIELD = "Awards"
BREAKDOWN_FIELD = "Leetify breakdown"
FORM_FIELD = "Recent form"
RANKS_FIELD = "Ranks"
WINRATE_FIELD = "Winrate"
MATCHES_FIELD = "Matches"
RATINGS_FIELD = "Ratings"
PROFILE_COLOUR = discord.Colour.blurple()
LEADERBOARD_COLOUR = discord.Colour.gold()
LEADERBOARD_FIELD = "Ranking"
LEADERBOARD_CONTINUED_FIELD = "Ranking (cont.)"
MAX_LEADERBOARD_ROWS = 25
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
UNRANKED = "-"
NOBODY_LINKED_MESSAGE = "Nobody is linked yet. Use `/link` to link your Steam account."
# Aim/positioning/utility are 0-100 scores; clutch/opening are signed ratings whose
# bar is full at this value (a guess until real API data is captured).
SCORE_MAX = 100.0
SIGNED_RATING_MAX = 0.2

OUTCOME_COLOURS: dict[MatchOutcome | None, discord.Colour] = {
    "win": discord.Colour.green(),
    "loss": discord.Colour.red(),
    "tie": discord.Colour.light_grey(),
    None: discord.Colour.light_grey(),
}


def build_match_embed(
    match: Match,
    awards: Sequence[Award],
    focus_steam_id: str | None = None,
    profile: Profile | None = None,
    identities: Mapping[str, Identity] | None = None,
) -> discord.Embed:
    """One embed for a whole match: score, team tables, awards and the focus player.

    Players in `identities` (linked server members) show by display name in the tables,
    and get a `Friends in this match` line with their mention (mentions are built only
    from stored numeric Discord IDs; the cog must send with `AllowedMentions.none()`).

    Players without a usable team number get their own `Other players` table, so nobody
    is dropped.

    The focus player's stats (and, with a profile, their Leetify breakdown and recent
    form) are only shown when they played in the match. Missing data is left out.
    """
    focus = next((p for p in match.players if p.steam64_id == focus_steam_id), None)
    embed = discord.Embed(
        title=_truncate(_title(match), TITLE_LIMIT),
        colour=OUTCOME_COLOURS[match_outcome(match, focus)],
        timestamp=match.finished_at,
    )
    names = {steam_id: i.display_name for steam_id, i in (identities or {}).items()}
    for name, team in zip(TEAM_NAMES, match.teams(), strict=True):
        is_focus_team = focus is not None and focus in team
        embed.add_field(
            name=YOUR_TEAM if is_focus_team else name,
            value=format_team_table(team, max_chars=FIELD_VALUE_LIMIT, names=names),
            inline=False,
        )
    unassigned = match.unassigned()
    if unassigned:
        embed.add_field(
            name=OTHER_PLAYERS_FIELD,
            value=format_team_table(unassigned, max_chars=FIELD_VALUE_LIMIT, names=names),
            inline=False,
        )
    if awards:
        lines = [f"**{a.title}**: {escape_name(a.name)} ({a.detail})" for a in awards]
        _add_field(embed, AWARDS_FIELD, "\n".join(lines), inline=False)
    friends = format_friends(match, identities or {}, focus)
    if friends:
        _add_field(embed, FRIENDS_FIELD, friends, inline=False)
    if focus is not None:
        focus_name = escape_name(focus.name or focus.steam64_id)
        stats = format_player_stats(focus)
        focus_identity = (identities or {}).get(focus.steam64_id)
        if focus_identity is not None:
            mention = f"<@{focus_identity.discord_id}> · {focus_name}"
            stats = f"{mention}\n{stats}" if stats else mention
        _add_field(embed, focus_name, stats)
        if profile is not None:
            _add_field(embed, BREAKDOWN_FIELD, format_breakdown(profile))
            if profile.recent_matches:
                _add_field(embed, FORM_FIELD, form_strip(profile.recent_matches))
    return embed


def format_friends(
    match: Match, identities: Mapping[str, Identity], focus: PlayerMatchStats | None = None
) -> str:
    """One `<@id> as SteamName (team)` line per linked member, by team then rating.

    At most `MAX_FRIEND_LINES` lines; lines that don't fit the field are dropped from the
    bottom (with a `+N more` line) so a mention is never cut in half.
    """
    groups = [*match.teams(), match.unassigned()]
    labels = [
        YOUR_TEAM if focus is not None and focus in team else name
        for name, team in zip(TEAM_NAMES, groups, strict=False)
    ] + [NO_TEAM]
    lines: list[str] = []
    for label, group in zip(labels, groups, strict=True):
        ranked = sorted(group, key=lambda p: (p.leetify_rating is None, -(p.leetify_rating or 0)))
        for player in ranked:
            identity = identities.get(player.steam64_id)
            if identity is None:
                continue
            name = escape_name(player.name or player.steam64_id)
            lines.append(f"<@{identity.discord_id}> as {name} ({label})")
    return _fit_lines(lines[:MAX_FRIEND_LINES], FIELD_VALUE_LIMIT)


def _fit_lines(lines: list[str], limit: int) -> str:
    """Join whole lines, dropping them from the bottom (+N more) until they fit."""
    for keep in range(len(lines), -1, -1):
        dropped = len(lines) - keep
        text = "\n".join(lines[:keep] + ([f"+{dropped} more"] if dropped else []))
        if len(text) <= limit:
            return text
    return ""


def match_outcome(match: Match, player: PlayerMatchStats | None) -> MatchOutcome | None:
    """Win/loss/tie for the player's team, or None when the player or scores are unknown."""
    if player is None or player.team_number is None:
        return None
    own = [s.score for s in match.team_scores if s.team_number == player.team_number]
    other = [s.score for s in match.team_scores if s.team_number != player.team_number]
    if len(own) != 1 or len(other) != 1:
        return None
    if own[0] == other[0]:
        return "tie"
    return "win" if own[0] > other[0] else "loss"


def format_player_stats(player: PlayerMatchStats) -> str:
    """The focus player's personal line-up of stats, one per line; empty when nothing is known."""
    lines: list[str] = []
    kda = (player.kills, player.deaths, player.assists)
    if any(v is not None for v in kda):
        lines.append("K/D/A: " + "/".join("-" if v is None else str(v) for v in kda))
    if player.adr is not None:
        lines.append(f"ADR: {player.adr:.0f}")
    if player.hs_percent is not None:
        lines.append(f"HS: {player.hs_percent:.0f}%")
    if player.leetify_rating is not None:
        lines.append(f"Rating: {format_rating(player.leetify_rating)}")
    if player.preaim is not None:
        lines.append(f"Preaim: {player.preaim:.1f}°")
    if player.reaction_time_ms is not None:
        lines.append(f"Reaction time: {player.reaction_time_ms:.0f} ms")
    multi = _multi_kills(player)
    if multi:
        lines.append(f"Multi-kills: {multi}")
    return "\n".join(lines)


def format_breakdown(profile: Profile) -> str:
    """Aim/positioning/utility (0-100 scores) and clutch/opening (signed ratings)."""
    lines = [
        f"{label}: {value:.0f}"
        for label, value in (
            ("Aim", profile.aim),
            ("Positioning", profile.positioning),
            ("Utility", profile.utility),
        )
        if value is not None
    ]
    lines += [
        f"{label}: {format_rating(value)}"
        for label, value in (("Clutch", profile.clutch), ("Opening", profile.opening))
        if value is not None
    ]
    return "\n".join(lines)


def build_profile_embed(profile: Profile) -> discord.Embed:
    """A player's ranks, winrate, Leetify rating bars and recent form; missing data is omitted."""
    embed = discord.Embed(
        title=escape_name(profile.name or profile.steam64_id),
        colour=PROFILE_COLOUR,
    )
    if profile.ranks is not None:
        _add_field(embed, RANKS_FIELD, format_ranks(profile.ranks))
    if profile.winrate is not None:
        _add_field(embed, WINRATE_FIELD, f"{profile.winrate * 100:.0f}%")
    if profile.total_matches is not None:
        _add_field(embed, MATCHES_FIELD, f"{profile.total_matches:,}")
    _add_field(embed, RATINGS_FIELD, format_rating_bars(profile), inline=False)
    if profile.recent_matches:
        _add_field(embed, FORM_FIELD, form_strip(profile.recent_matches), inline=False)
    return embed


def build_leaderboard_embed(rows: Sequence[LeaderboardRow], metric: Metric) -> discord.Embed:
    """The squad ranked by `metric`: one line per player (medals for the top 3).

    Lines are packed into fields of at most `FIELD_VALUE_LIMIT` characters without
    splitting a line; at most `MAX_LEADERBOARD_ROWS` players are shown (`+N more` footer).
    Mentions are built only from stored numeric Discord IDs; send with
    `AllowedMentions.none()`.
    """
    embed = discord.Embed(
        title=f"CS2 leaderboard: {metric_label(metric)}",
        colour=LEADERBOARD_COLOUR,
    )
    if not rows:
        embed.description = NOBODY_LINKED_MESSAGE
        return embed
    lines = [format_leaderboard_row(row, metric) for row in rows[:MAX_LEADERBOARD_ROWS]]
    for index, chunk in enumerate(_pack_lines(lines, FIELD_VALUE_LIMIT)):
        name = LEADERBOARD_FIELD if index == 0 else LEADERBOARD_CONTINUED_FIELD
        embed.add_field(name=name, value=chunk, inline=False)
    hidden = len(rows) - MAX_LEADERBOARD_ROWS
    if hidden > 0:
        embed.set_footer(text=f"+{hidden} more")
    return embed


def format_leaderboard_row(row: LeaderboardRow, metric: Metric) -> str:
    """`🥇 <@id> Name · **+0.05** ↑ +0.03 🔥3`; unranked rows start with `-`."""
    if row.rank is None:
        position = UNRANKED
    else:
        position = MEDALS.get(row.rank, f"{row.rank}.")
    text = (
        f"{position} <@{row.discord_id}> {escape_name(row.name)}"
        f" · **{format_metric_value(metric, row.value)}**"
    )
    trend = format_trend(row.trend)
    return f"{text} {trend}" if trend else text


def _pack_lines(lines: Sequence[str], limit: int) -> list[str]:
    """Join lines into chunks of at most `limit` characters, never splitting a line.

    A single line longer than `limit` is truncated on its own.
    """
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = _truncate(line, limit)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks


def format_ranks(ranks: Ranks) -> str:
    """Premier, FACEIT and Leetify ranks, one per line; only the known ones."""
    lines: list[str] = []
    if ranks.premier is not None:
        lines.append(f"Premier: {ranks.premier:,}")
    if ranks.faceit_level is not None:
        lines.append(f"FACEIT: level {ranks.faceit_level}")
    if ranks.leetify is not None:
        lines.append(f"Leetify: {ranks.leetify:.2f}")
    return "\n".join(lines)


def format_rating_bars(profile: Profile) -> str:
    """The five Leetify ratings as bars in a code block; empty when none are known."""
    rows = [
        f"{label:<11} {rating_bar(value, SCORE_MAX)} {value:>5.0f}"
        for label, value in (
            ("Aim", profile.aim),
            ("Positioning", profile.positioning),
            ("Utility", profile.utility),
        )
        if value is not None
    ]
    rows += [
        f"{label:<11} {rating_bar(value, SIGNED_RATING_MAX)} {format_rating(value):>5}"
        for label, value in (("Clutch", profile.clutch), ("Opening", profile.opening))
        if value is not None
    ]
    if not rows:
        return ""
    return "```\n" + "\n".join(rows) + "\n```"


def _title(match: Match) -> str:
    map_name = match.map_name or UNKNOWN_MAP
    if len(match.team_scores) != 2:
        return map_name
    first, second = match.team_scores
    return f"{map_name} - {first.score}:{second.score}"


def _multi_kills(player: PlayerMatchStats) -> str:
    counts = (
        ("2k", player.multi2k),
        ("3k", player.multi3k),
        ("4k", player.multi4k),
        ("5k", player.multi5k),
    )
    return " · ".join(f"{count}× {label}" for label, count in counts if count)


def _add_field(embed: discord.Embed, name: str, value: str, inline: bool = True) -> None:
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
