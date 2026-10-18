from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

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
from ninja_bot.cs2.prices import Currency, Prices, format_price
from ninja_bot.cs2.squad import (
    LeaderboardRow,
    Metric,
    SquadMember,
    WorthRow,
    format_metric_value,
    format_trend,
    metric_label,
)
from ninja_bot.cs2.valuation import Valuation, ValuedItem

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
SQUAD_FIELD = "Squad"
MAX_SQUAD_LINES = 10
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

TOP_SKINS_FIELD = "Top skins"
WORTH_FIELD = "Inventory worth"
TOTAL_WORTH_FIELD = "Total worth"
ITEMS_FIELD = "Items"
INVENTORY_TOP_ITEMS = 5
INVENTORY_TOP_FIELD = f"Top {INVENTORY_TOP_ITEMS} items"
INVENTORY_COLOUR = discord.Colour.dark_gold()
EMPTY_INVENTORY_MESSAGE = "No marketable items in this inventory."
# Long enough for `★ StatTrak™ Butterfly Knife | Case Hardened (Battle-Scarred)`.
SKIN_NAME_LENGTH = 64
SKINBOARD_TITLE = "CS2 skinboard: inventory worth"
NO_PUBLIC_INVENTORIES_MESSAGE = "No linked member has a public inventory to rank."


@dataclass(frozen=True)
class MatchSkins:
    """The priciest skins of a match's players (`ValuedItem.owner` = steam64 id)."""

    items: Sequence[ValuedItem]
    currency: Currency


@dataclass(frozen=True)
class ProfileSkins:
    """One player's inventory worth and top skins."""

    valuation: Valuation
    currency: Currency


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
    skins: MatchSkins | None = None,
) -> discord.Embed:
    """One embed for a whole match: score, team tables, awards and the focus player.

    Players in `identities` (linked server members) show by display name in the tables,
    and get a `Friends in this match` line with their mention (mentions are built only
    from stored numeric Discord IDs; the cog must send with `AllowedMentions.none()`).

    Players without a usable team number get their own `Other players` table, so nobody
    is dropped.

    The focus player's stats (and, with a profile, their Leetify breakdown and recent
    form) are only shown when they played in the match. Missing data is left out.
    With `skins`, a `Top skins` field lists them with their owners.
    """
    focus = next((p for p in match.players if p.steam64_id == focus_steam_id), None)
    embed = discord.Embed(
        title=_truncate(_title(match), TITLE_LIMIT),
        colour=OUTCOME_COLOURS[match_outcome(match, focus)],
        timestamp=match.finished_at,
    )
    _add_tables_and_awards(embed, match, awards, identities or {}, focus)
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
    _add_match_skins(embed, match, identities or {}, skins)
    return embed


def build_announcement_embed(
    match: Match,
    awards: Sequence[Award],
    squad: Sequence[SquadMember],
    identities: Mapping[str, Identity] | None = None,
    skins: MatchSkins | None = None,
) -> discord.Embed:
    """An auto-posted scoreboard: like `build_match_embed`, but for the whole squad.

    Squad members who played are highlighted in the tables and listed in a `Squad` field
    (members not in the match are ignored). The colour follows the team most of the squad
    played on; a squad split evenly across teams, or a tie, is grey. There is no
    per-player breakdown. Mentions are built only from stored numeric Discord IDs; send
    with `AllowedMentions.none()`.

    `identities` is authoritative when given: squad members without one (e.g. they left
    the server) are listed by Steam name without a mention. With `identities=None`
    (resolution failed) every squad member keeps a mention. With `skins`, a `Top skins`
    field lists them with their owners (names only, no mentions).
    """
    in_match = {p.steam64_id: p for p in match.players}
    members = [m for m in squad if m.steam64_id in in_match]
    players = [in_match[steam_id] for steam_id in dict.fromkeys(m.steam64_id for m in members)]
    embed = discord.Embed(
        title=_truncate(_title(match), TITLE_LIMIT),
        colour=OUTCOME_COLOURS[squad_outcome(match, players)],
        timestamp=match.finished_at,
    )
    highlight = {p.steam64_id for p in players}
    _add_tables_and_awards(embed, match, awards, identities or {}, highlight=highlight)
    _add_field(embed, SQUAD_FIELD, format_squad(members, in_match, identities), inline=False)
    _add_match_skins(embed, match, identities or {}, skins)
    return embed


def _add_match_skins(
    embed: discord.Embed,
    match: Match,
    identities: Mapping[str, Identity],
    skins: MatchSkins | None,
) -> None:
    """The `Top skins` field; owners by display name, else Steam name, else steam64 id."""
    if skins is None:
        return
    owner_names = {p.steam64_id: p.name or p.steam64_id for p in match.players}
    owner_names.update({steam_id: i.display_name for steam_id, i in identities.items()})
    _add_field(
        embed,
        TOP_SKINS_FIELD,
        skins_field_lines(skins.items, skins.currency, owner_names),
        inline=False,
    )


def format_prices(prices: Prices, currency: Currency) -> str:
    """`Steam €12.30 · Skinport €10.05`; a missing source shows `n/a`."""
    return (
        f"Steam {format_price(prices.steam, currency)}"
        f" · Skinport {format_price(prices.skinport, currency)}"
    )


def skins_field_lines(
    valued: Sequence[ValuedItem],
    currency: Currency,
    owner_names: Mapping[str, str] | None = None,
) -> str:
    """One `**Name** ×2 · Steam €X · Skinport €Y · Owner` line per item (unit prices).

    The owner is shown only with `owner_names` (steam64 id -> name; unknown owners are
    left out). Whole lines are dropped from the bottom (`+N more`) to fit the field.
    """
    lines: list[str] = []
    for item in valued:
        line = f"**{escape_name(item.name, SKIN_NAME_LENGTH)}**"
        if item.count > 1:
            line += f" ×{item.count}"
        line += f" · {format_prices(item.prices, currency)}"
        owner = None if owner_names is None or item.owner is None else owner_names.get(item.owner)
        if owner is not None:
            line += f" · {escape_name(owner)}"
        lines.append(line)
    return _fit_lines(lines, FIELD_VALUE_LIMIT)


def build_inventory_embed(
    name: str, valuation: Valuation, currency: Currency, steam_url: str | None = None
) -> discord.Embed:
    """`/inventory`: total worth per source, item counts and the top skins.

    An inventory without marketable items gets `EMPTY_INVENTORY_MESSAGE` instead.
    """
    embed = discord.Embed(
        title=_truncate(f"CS2 inventory: {escape_name(name)}", TITLE_LIMIT),
        colour=INVENTORY_COLOUR,
        url=steam_url,
    )
    if valuation.item_count == 0:
        embed.description = EMPTY_INVENTORY_MESSAGE
        return embed
    _add_field(embed, TOTAL_WORTH_FIELD, format_worth(valuation, currency), inline=False)
    _add_field(embed, ITEMS_FIELD, format_item_counts(valuation), inline=False)
    _add_field(
        embed,
        INVENTORY_TOP_FIELD,
        skins_field_lines(valuation.top[:INVENTORY_TOP_ITEMS], currency),
        inline=False,
    )
    return embed


def build_skinboard_embed(
    rows: Sequence[WorthRow],
    currency: Currency,
    empty_message: str = NO_PUBLIC_INVENTORIES_MESSAGE,
) -> discord.Embed:
    """`/skinboard`: one line per member, richest first (medals for the top 3).

    Lines are packed like `/leaderboard`, at most `MAX_LEADERBOARD_ROWS` (`+N more` footer);
    no rows = `empty_message`. Send with `AllowedMentions.none()`.
    """
    embed = discord.Embed(title=SKINBOARD_TITLE, colour=INVENTORY_COLOUR)
    if not rows:
        embed.description = empty_message
        return embed
    lines = [format_skinboard_row(row, currency) for row in rows[:MAX_LEADERBOARD_ROWS]]
    for index, chunk in enumerate(pack_lines(lines, FIELD_VALUE_LIMIT)):
        name = LEADERBOARD_FIELD if index == 0 else LEADERBOARD_CONTINUED_FIELD
        embed.add_field(name=name, value=chunk, inline=False)
    hidden = len(rows) - MAX_LEADERBOARD_ROWS
    if hidden > 0:
        embed.set_footer(text=f"+{hidden} more")
    return embed


def format_skinboard_row(row: WorthRow, currency: Currency) -> str:
    """`🥇 <@id> Name · Steam **€1,234.50** · Skinport €1,100.00`."""
    entry = row.entry
    text = f"{MEDALS.get(row.rank, f'{row.rank}.')} <@{entry.discord_id}>"
    if entry.name:
        text += f" {escape_name(entry.name)}"
    return (
        f"{text} · Steam **{format_price(entry.steam_total, currency)}**"
        f" · Skinport {format_price(entry.skinport_total, currency)}"
    )


def format_worth(valuation: Valuation, currency: Currency) -> str:
    """Both totals: `Steam €X · Skinport €Y`."""
    return format_prices(Prices(valuation.steam_total, valuation.skinport_total), currency)


def format_item_counts(valuation: Valuation) -> str:
    """`12 marketable · 2 without a price` (units, not stacks)."""
    unpriced = valuation.item_count - valuation.priced_count
    return f"{valuation.item_count:,} marketable · {unpriced:,} without a price"


def squad_outcome(match: Match, players: Sequence[PlayerMatchStats]) -> MatchOutcome | None:
    """The result of the team most of `players` played on; None if split evenly or unknown."""
    counts = Counter(p.team_number for p in players if p.team_number is not None).most_common()
    if not counts or (len(counts) > 1 and counts[0][1] == counts[1][1]):
        return None
    team = counts[0][0]
    return match_outcome(match, next(p for p in players if p.team_number == team))


def format_squad(
    members: Sequence[SquadMember],
    players: Mapping[str, PlayerMatchStats],
    identities: Mapping[str, Identity] | None = None,
) -> str:
    """One `K/D/A · ADR · rating` line per member, best rating first (max 10).

    Without `identities` every line starts with `<@id>`. With them, members who have an
    identity get `<@id> as SteamName · `, the others just `SteamName · ` (no dead mention).
    """
    ranked = sorted(
        dict.fromkeys(members),
        key=lambda m: (
            players[m.steam64_id].leetify_rating is None,
            -(players[m.steam64_id].leetify_rating or 0),
        ),
    )
    lines = [_squad_line(m, players[m.steam64_id], identities) for m in ranked[:MAX_SQUAD_LINES]]
    return _fit_lines(lines, FIELD_VALUE_LIMIT)


def _squad_line(
    member: SquadMember, player: PlayerMatchStats, identities: Mapping[str, Identity] | None
) -> str:
    stats = format_squad_stats(player)
    if identities is None:
        return f"<@{member.discord_id}> {stats}"
    name = escape_name(player.name or player.steam64_id)
    identity = identities.get(member.steam64_id)
    if identity is None:
        return f"{name} · {stats}"
    return f"<@{identity.discord_id}> as {name} · {stats}"


def format_squad_stats(player: PlayerMatchStats) -> str:
    """`20/15/4 · 83 ADR · +0.05`, with `-` for unknown values."""
    kda = "/".join(
        "-" if v is None else str(v) for v in (player.kills, player.deaths, player.assists)
    )
    adr = "-" if player.adr is None else f"{player.adr:.0f}"
    return f"{kda} · {adr} ADR · {format_rating(player.leetify_rating)}"


def _add_tables_and_awards(
    embed: discord.Embed,
    match: Match,
    awards: Sequence[Award],
    identities: Mapping[str, Identity],
    focus: PlayerMatchStats | None = None,
    highlight: Collection[str] = (),
) -> None:
    """Team tables (the focus player's team is `Your team`), `Other players`, then awards."""
    names = {steam_id: i.display_name for steam_id, i in identities.items()}
    for name, team in zip(TEAM_NAMES, match.teams(), strict=True):
        is_focus_team = focus is not None and focus in team
        embed.add_field(
            name=YOUR_TEAM if is_focus_team else name,
            value=format_team_table(
                team, max_chars=FIELD_VALUE_LIMIT, names=names, highlight=highlight
            ),
            inline=False,
        )
    unassigned = match.unassigned()
    if unassigned:
        embed.add_field(
            name=OTHER_PLAYERS_FIELD,
            value=format_team_table(
                unassigned, max_chars=FIELD_VALUE_LIMIT, names=names, highlight=highlight
            ),
            inline=False,
        )
    if awards:
        lines = [f"**{a.title}**: {escape_name(a.name)} ({a.detail})" for a in awards]
        _add_field(embed, AWARDS_FIELD, "\n".join(lines), inline=False)


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


def build_profile_embed(profile: Profile, skins: ProfileSkins | None = None) -> discord.Embed:
    """A player's ranks, winrate, Leetify rating bars and recent form; missing data is omitted.

    With `skins`, an `Inventory worth` field (`EMPTY_INVENTORY_MESSAGE` without marketable
    items) and a `Top skins` field are added at the end.
    """
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
    if skins is not None:
        worth = (
            format_worth(skins.valuation, skins.currency)
            if skins.valuation.item_count
            else EMPTY_INVENTORY_MESSAGE
        )
        _add_field(embed, WORTH_FIELD, worth, inline=False)
        _add_field(
            embed,
            TOP_SKINS_FIELD,
            skins_field_lines(skins.valuation.top, skins.currency),
            inline=False,
        )
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
    for index, chunk in enumerate(pack_lines(lines, FIELD_VALUE_LIMIT)):
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


def pack_lines(lines: Sequence[str], limit: int) -> list[str]:
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
