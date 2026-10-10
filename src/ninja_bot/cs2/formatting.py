from __future__ import annotations

import math
from collections.abc import Sequence

from ninja_bot.cs2.models import MatchOutcome, PlayerMatchStats, RecentMatch

NAME_WIDTH = 12
MISSING = "-"
STAR = "★"
FORM_LENGTH = 5
FORM_EMOJI: dict[MatchOutcome | None, str] = {
    "win": "🟩",
    "loss": "🟥",
    "tie": "🟨",
    None: "⬜",
}
NO_RECENT_MATCHES = "No recent matches"
NO_PLAYERS = "No players"
BAR_WIDTH = 10
BAR_FILLED = "█"
BAR_EMPTY = "░"

_CODE_START = "```\n"
_CODE_END = "\n```"
_ROW_FORMAT = "{star}{name:<12} {kda:>8} {adr:>4} {hs:>4} {rating:>6}"


def format_rating(value: float | None) -> str:
    """A signed Leetify rating with 2 decimals (`+0.05`, `-0.03`), or `-` when unknown."""
    if value is None:
        return MISSING
    rounded = round(value, 2)
    # Avoid "-0.00" for tiny negative ratings.
    return f"{rounded:+.2f}" if rounded else "+0.00"


def rating_bar(value: float | None, max_value: float, width: int = BAR_WIDTH) -> str:
    """A fixed-width bar for `value` out of `max_value`; None and negatives are empty.

    Values above `max_value` give a full bar. Raises ValueError for a non-positive
    `max_value` or `width`.
    """
    if max_value <= 0 or width <= 0:
        raise ValueError("max_value and width must be positive")
    if value is None or math.isnan(value):
        filled = 0
    else:
        filled = round(min(max(value / max_value, 0.0), 1.0) * width)
    return BAR_FILLED * filled + BAR_EMPTY * (width - filled)


def format_team_table(players: Sequence[PlayerMatchStats], max_chars: int = 1000) -> str:
    """One team as a monospace code block: name, K/D/A, ADR, HS% and rating per player.

    The player with the most MVPs (ties: best rating) gets a star. Rows are dropped from
    the bottom with a `+N more` line so the result never exceeds `max_chars`; raises
    ValueError when not even the header fits.
    """
    header = _ROW_FORMAT.format(
        star=" ", name="Player", kda="K/D/A", adr="ADR", hs="HS%", rating="Rating"
    )
    if not players:
        return _code_block([header, NO_PLAYERS], max_chars)
    star = _star_player(players)
    rows = [_row(player, player is star) for player in players]
    for shown in range(len(rows), -1, -1):
        lines = [header, *rows[:shown]]
        if shown < len(rows):
            lines.append(f"+{len(rows) - shown} more")
        table = "\n".join(lines)
        if len(_CODE_START) + len(table) + len(_CODE_END) <= max_chars:
            return _CODE_START + table + _CODE_END
    raise ValueError(f"max_chars={max_chars} is too small for a team table")


def form_strip(recent: Sequence[RecentMatch]) -> str:
    """The newest up-to-5 results as emoji, newest first, plus their average rating."""
    matches = recent[:FORM_LENGTH]
    if not matches:
        return NO_RECENT_MATCHES
    emoji = "".join(FORM_EMOJI[match.outcome] for match in matches)
    ratings = [m.leetify_rating for m in matches if m.leetify_rating is not None]
    if not ratings:
        return emoji
    return f"{emoji} avg {format_rating(sum(ratings) / len(ratings))}"


def clean_name(name: str | None) -> str:
    """A player name safe for a code block, truncated to `NAME_WIDTH` characters."""
    cleaned = "".join(c for c in name or "" if c != "`" and c.isprintable()).strip()
    if not cleaned:
        return "?"
    if len(cleaned) > NAME_WIDTH:
        return cleaned[: NAME_WIDTH - 1] + "…"
    return cleaned


def _row(player: PlayerMatchStats, starred: bool) -> str:
    kda = "/".join(_int(v) for v in (player.kills, player.deaths, player.assists))
    return _ROW_FORMAT.format(
        star=STAR if starred else " ",
        name=clean_name(player.name),
        kda=kda,
        adr=_int(player.adr),
        hs=_int(player.hs_percent),
        rating=format_rating(player.leetify_rating),
    )


def _star_player(players: Sequence[PlayerMatchStats]) -> PlayerMatchStats | None:
    """Most MVPs, then best rating; None when neither is known for anyone."""
    candidates = [p for p in players if p.mvps is not None or p.leetify_rating is not None]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda p: (
            p.mvps is not None,
            p.mvps or 0,
            p.leetify_rating is not None,
            p.leetify_rating or 0.0,
        ),
    )


def _int(value: float | None) -> str:
    return MISSING if value is None else str(round(value))


def _code_block(lines: list[str], max_chars: int) -> str:
    block = _CODE_START + "\n".join(lines) + _CODE_END
    if len(block) > max_chars:
        raise ValueError(f"max_chars={max_chars} is too small for a team table")
    return block
