"""Discord embeds for session polls, round results, confirmed sessions and `/sessions`.

Pure functions over plain data (no store, no members). Mentions are built only from stored
numeric Discord IDs and never go inside code blocks; times use Discord timestamps so every
viewer sees their own time zone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from zoneinfo import ZoneInfo

import discord

from ninja_bot.cs2.embed import FIELD_NAME_LIMIT, FIELD_VALUE_LIMIT, TITLE_LIMIT, pack_lines
from ninja_bot.cs2.formatting import escape_name
from ninja_bot.sessions import content
from ninja_bot.sessions.scoring import MAX_ROUNDS, SlotScore, Vote, Votes
from ninja_bot.sessions.service import MAX_NAME_LENGTH, FailReason
from ninja_bot.sessions.slots import format_local_time
from ninja_bot.sessions.store import STATUS_AWAITING_CONFIRM, Poll, PollSlot, Session

DESCRIPTION_LIMIT = 4096
POLL_COLOUR = discord.Colour.blurple()
RESULT_COLOUR = discord.Colour.green()
NO_FIT_COLOUR = discord.Colour.red()
SESSION_COLOUR = discord.Colour.gold()
# Upcoming sessions shown by /sessions (open polls are capped by MAX_OPEN_POLLS).
MAX_LIST_SESSIONS = 10


def timestamp(unix: float, style: str) -> str:
    """A Discord timestamp (`F` full date and time, `R` relative, `d` short date)."""
    return f"<t:{int(unix)}:{style}>"


# Discord's limit for a select option label.
SELECT_LABEL_LIMIT = 100


def format_slot_label(slot: PollSlot, tz: ZoneInfo) -> str:
    """`Fri 17 Oct 19:00` in `tz`, for vote select options (they can't hold `<t:...>`).

    English names regardless of the locale; cut to `SELECT_LABEL_LIMIT`.
    """
    return format_local_time(slot.start, tz)[:SELECT_LABEL_LIMIT]


def mention(user_id: int) -> str:
    return f"<@{user_id}>"


def poll_title(poll: Poll) -> str:
    return escape_name(poll.name, MAX_NAME_LENGTH)


def build_poll_embed(
    poll: Poll,
    slots: Sequence[PollSlot],
    scores: Sequence[SlotScore],
    players: Sequence[int],
    pending: Sequence[int],
    names: Mapping[int, str] | None = None,
) -> discord.Embed:
    """The live poll message for the current round.

    One line per slot (in `slots` order, numbered like the vote options) with its yes /
    if-needed counts; `Joined` lists every player (`names` adds a display name after the
    mention, for clients that show unknown mentions as raw ids), `Waiting for` the players
    without a vote this round (`+N more` when it doesn't fit).
    """
    embed = discord.Embed(title=poll_title(poll), colour=POLL_COLOUR)
    by_slot = {score.slot_id: score for score in scores}
    header = [
        f"**{content.ROUND_LABEL.format(round=poll.round_number, max=MAX_ROUNDS)}**",
        _poll_details(poll),
        "",
    ]
    slot_lines = []
    for index, slot in enumerate(slots, start=1):
        score = by_slot.get(slot.id)
        slot_lines.append(
            content.SLOT_LINE.format(
                index=index,
                start=timestamp(slot.start, "F"),
                yes=score.yes if score else 0,
                if_needed=score.if_needed if score else 0,
            )
        )
    if poll.round_hours:
        deadline = poll.round_started_at + poll.round_hours * 3600
        slot_lines += ["", content.DEADLINE_LINE.format(deadline=timestamp(deadline, "R"))]
    embed.description = _truncate("\n".join(header + slot_lines), DESCRIPTION_LIMIT)

    joined = [_person(user_id, names) for user_id in players]
    for index, chunk in enumerate(pack_lines(joined, FIELD_VALUE_LIMIT)):
        name = content.JOINED_FIELD if index == 0 else content.JOINED_CONTINUED_FIELD
        _add_field(embed, name, chunk)
    _add_field(embed, content.WAITING_FIELD, fit_with_more([mention(p) for p in pending]))
    embed.set_footer(text=content.POLL_FOOTER)
    return embed


def build_result_embed(
    poll: Poll,
    top: Sequence[SlotScore],
    slots: Sequence[PollSlot],
    players: Sequence[int],
    votes: Votes,
) -> discord.Embed:
    """The fitting slots of a closed round (best first) and who can't make each one.

    A player can't make a slot when they voted No or didn't vote on it.
    """
    by_id = {slot.id: slot for slot in slots}
    lines = [content.RESULT_DESCRIPTION.format(gm=mention(poll.gm_id)), ""]
    for rank, score in enumerate(top, start=1):
        slot = by_id.get(score.slot_id)
        if slot is None:
            continue
        lines.append(
            content.RESULT_SLOT_LINE.format(
                rank=rank,
                start=timestamp(slot.start, "F"),
                yes=score.yes,
                if_needed=score.if_needed,
            )
        )
        absent = [
            mention(player)
            for player in players
            if votes.get((player, slot.id)) not in (Vote.YES, Vote.IF_NEEDED)
        ]
        lines.append(
            content.CANT_MAKE_IT_LINE.format(players=", ".join(absent))
            if absent
            else content.EVERYONE_CAN_LINE
        )
    return discord.Embed(
        title=poll_title(poll),
        description=_truncate("\n".join(lines), DESCRIPTION_LIMIT),
        colour=RESULT_COLOUR,
    )


def build_next_round_embed(
    poll: Poll, slots: Sequence[PollSlot], pending: Sequence[int]
) -> discord.Embed:
    """A round found nothing; `poll` is already in its next round with `slots`."""
    embed = discord.Embed(title=poll_title(poll), colour=POLL_COLOUR)
    if slots:
        embed.description = content.NEXT_ROUND_DESCRIPTION.format(
            round=poll.round_number,
            max=MAX_ROUNDS,
            slots=len(slots),
            first=timestamp(min(slot.start for slot in slots), "d"),
            last=timestamp(max(slot.start for slot in slots), "d"),
        )
    _add_field(
        embed, content.NEXT_ROUND_PENDING_FIELD, fit_with_more([mention(p) for p in pending])
    )
    return embed


def build_no_fit_embed(poll: Poll, reason: FailReason = FailReason.LAST_ROUND) -> discord.Embed:
    """The poll ended without a date, with advice for the next attempt."""
    if reason is FailReason.NO_SLOTS:
        summary = content.NO_SLOTS_DESCRIPTION
    else:
        summary = content.NO_FIT_DESCRIPTION.format(rounds=poll.round_number)
    return discord.Embed(
        title=poll_title(poll),
        description=f"{summary}\n{content.NO_FIT_ADVICE}",
        colour=NO_FIT_COLOUR,
    )


def build_session_embed(session: Session) -> discord.Embed:
    """A confirmed session: when (absolute and relative), GM, length and roster."""
    embed = discord.Embed(
        title=_truncate(
            content.SESSION_TITLE.format(name=escape_name(session.name, MAX_NAME_LENGTH)),
            TITLE_LIMIT,
        ),
        description=_session_when(session),
        colour=SESSION_COLOUR,
    )
    _add_field(embed, content.SESSION_GM_FIELD, mention(session.gm_id), inline=True)
    hours = (session.end - session.start) / 3600
    _add_field(
        embed,
        content.SESSION_LENGTH_FIELD,
        content.LENGTH_LABEL.format(hours=_hours(hours)),
        inline=True,
    )
    roster = [mention(user_id) for user_id in session.roster]
    for index, chunk in enumerate(pack_lines(roster, FIELD_VALUE_LIMIT)):
        name = (
            content.SESSION_ROSTER_FIELD if index == 0 else content.SESSION_ROSTER_CONTINUED_FIELD
        )
        _add_field(embed, name, chunk)
    return embed


def build_list_embed(
    polls: Sequence[Poll],
    sessions: Sequence[Session],
    pending: Mapping[int, Sequence[int]] | None = None,
) -> discord.Embed:
    """`/sessions`: open polls (linked, with round and who is pending, from `pending` keyed
    by poll id) and upcoming sessions (at most `MAX_LIST_SESSIONS`, `+N more`)."""
    embed = discord.Embed(title=content.LIST_TITLE, colour=POLL_COLOUR)
    if not polls and not sessions:
        embed.description = content.LIST_EMPTY_MESSAGE
        return embed
    poll_lines = [_list_poll_line(poll, (pending or {}).get(poll.id, ())) for poll in polls]
    _add_field(embed, content.LIST_POLLS_FIELD, fit_with_more(poll_lines))
    session_lines = [
        content.LIST_SESSION_LINE.format(
            name=escape_name(session.name, MAX_NAME_LENGTH),
            start=timestamp(session.start, "F"),
            relative=timestamp(session.start, "R"),
            gm=mention(session.gm_id),
        )
        for session in sessions[:MAX_LIST_SESSIONS]
    ]
    hidden = max(0, len(sessions) - MAX_LIST_SESSIONS)
    _add_field(embed, content.LIST_SESSIONS_FIELD, fit_with_more(session_lines, hidden=hidden))
    return embed


def poll_link(poll: Poll) -> str | None:
    if poll.message_id is None:
        return None
    return f"https://discord.com/channels/{poll.guild_id}/{poll.channel_id}/{poll.message_id}"


def fit_with_more(lines: Sequence[str], limit: int = FIELD_VALUE_LIMIT, hidden: int = 0) -> str:
    """As many whole lines as fit in `limit`, then `+N more` for the rest (plus `hidden`
    lines the caller already left out)."""
    if not hidden:
        chunks = pack_lines(lines, limit)
        if len(chunks) <= 1:
            return chunks[0] if chunks else ""
    shown = list(lines)
    while True:
        more = content.MORE_LINE.format(count=len(lines) - len(shown) + hidden)
        text = "\n".join([*shown, more])
        if len(text) <= limit or not shown:
            return _truncate(text, limit)
        shown.pop()


def _list_poll_line(poll: Poll, pending: Sequence[int]) -> str:
    name = poll_title(poll)
    link = poll_link(poll)
    shown = f"[{name}]({link})" if link else f"**{name}**"
    line = content.LIST_POLL_LINE.format(
        name=shown, round=content.ROUND_LABEL.format(round=poll.round_number, max=MAX_ROUNDS)
    )
    if poll.status == STATUS_AWAITING_CONFIRM:
        state = content.LIST_POLL_AWAITING_CONFIRM
    elif pending:
        state = content.LIST_POLL_WAITING.format(players=", ".join(mention(p) for p in pending))
    else:
        state = content.LIST_POLL_ALL_VOTED
    return f"{line}{content.DESCRIPTION_SEPARATOR}{state}"


def _poll_details(poll: Poll) -> str:
    parts = [
        content.GM_LABEL.format(gm=mention(poll.gm_id)),
        content.DURATION_LABEL.format(hours=_hours(poll.duration_hours)),
    ]
    if poll.min_players:
        parts.append(content.MIN_PLAYERS_LABEL.format(count=poll.min_players))
    return content.DESCRIPTION_SEPARATOR.join(parts)


def _session_when(session: Session) -> str:
    return content.SESSION_WHEN.format(
        start=timestamp(session.start, "F"), relative=timestamp(session.start, "R")
    )


def _person(user_id: int, names: Mapping[int, str] | None) -> str:
    name = (names or {}).get(user_id)
    if not name:
        return mention(user_id)
    return f"{mention(user_id)} {escape_name(name)}"


def _hours(hours: float) -> str:
    return f"{hours:g}"


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
