from __future__ import annotations

from collections.abc import Sequence

import discord

from ninja_bot.cs2.awards import Award
from ninja_bot.cs2.formatting import form_strip, format_rating, format_team_table
from ninja_bot.cs2.models import Match, MatchOutcome, PlayerMatchStats, Profile

# Discord embed limits.
TITLE_LIMIT = 256
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024

UNKNOWN_MAP = "Unknown map"
TEAM_NAMES = ("Team A", "Team B")
YOUR_TEAM = "Your team"
AWARDS_FIELD = "Awards"
BREAKDOWN_FIELD = "Leetify breakdown"
FORM_FIELD = "Recent form"

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
) -> discord.Embed:
    """One embed for a whole match: score, both team tables, awards and the focus player.

    The focus player's stats (and, with a profile, their Leetify breakdown and recent
    form) are only shown when they played in the match. Missing data is left out.
    """
    focus = next((p for p in match.players if p.steam64_id == focus_steam_id), None)
    embed = discord.Embed(
        title=_truncate(_title(match), TITLE_LIMIT),
        colour=OUTCOME_COLOURS[match_outcome(match, focus)],
        timestamp=match.finished_at,
    )
    for name, team in zip(TEAM_NAMES, match.teams(), strict=True):
        is_focus_team = focus is not None and focus in team
        embed.add_field(
            name=YOUR_TEAM if is_focus_team else name,
            value=format_team_table(team, max_chars=FIELD_VALUE_LIMIT),
            inline=False,
        )
    if awards:
        lines = [f"**{a.title}**: {a.name} ({a.detail})" for a in awards]
        _add_field(embed, AWARDS_FIELD, "\n".join(lines), inline=False)
    if focus is not None:
        _add_field(embed, focus.name or focus.steam64_id, format_player_stats(focus))
        if profile is not None:
            _add_field(embed, BREAKDOWN_FIELD, format_breakdown(profile))
            if profile.recent_matches:
                _add_field(embed, FORM_FIELD, form_strip(profile.recent_matches))
    return embed


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
