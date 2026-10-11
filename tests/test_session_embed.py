from __future__ import annotations

import re
from datetime import date, time

import discord

from ninja_bot.cs2.embed import FIELD_VALUE_LIMIT
from ninja_bot.sessions import content
from ninja_bot.sessions.embed import (
    MAX_LIST_SESSIONS,
    build_list_embed,
    build_next_round_embed,
    build_no_fit_embed,
    build_poll_embed,
    build_result_embed,
    build_session_embed,
    fit_with_more,
)
from ninja_bot.sessions.scoring import MAX_PLAYERS, SlotScore, Vote
from ninja_bot.sessions.service import MAX_NAME_LENGTH, FailReason
from ninja_bot.sessions.slots import MAX_SLOTS
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_VOTING,
    Poll,
    PollSlot,
    Session,
)

EMBED_TOTAL_LIMIT = 6000
DESCRIPTION_LIMIT = 4096
START = 1_793_000_000.0
HOUR = 3600.0
GM = 100
EVIL_NAME = "[evil](https://x.y) **bold**"
# Long Discord-ish ids so mentions are as long as real ones.
BIG_ID = 900_000_000_000_000_000


def make_poll(
    name: str = "Curse of Strahd",
    round_number: int = 1,
    min_players: int = 0,
    round_hours: float = 0,
    status: str = STATUS_VOTING,
    message_id: int | None = 555,
    poll_id: int = 7,
) -> Poll:
    return Poll(
        id=poll_id,
        guild_id=1,
        channel_id=2,
        message_id=message_id,
        name=name,
        gm_id=GM,
        days=(4, 5),
        times=(time(19),),
        weeks=2,
        duration_hours=4,
        min_players=min_players,
        round_hours=round_hours,
        round_number=round_number,
        window_start=date(2026, 11, 3),
        status=status,
        created_at=START - 10 * HOUR,
        round_started_at=START - 5 * HOUR,
    )


def make_slots(count: int) -> list[PollSlot]:
    return [
        PollSlot(id=i + 1, poll_id=7, round_number=1, start=START + i * 24 * HOUR, end=0)
        for i in range(count)
    ]


def score(slot_id: int, yes: int = 0, if_needed: int = 0) -> SlotScore:
    return SlotScore(slot_id, yes, if_needed, 0, 0, yes + if_needed)


def make_session(roster: tuple[int, ...] = (GM, 101), name: str = "Curse of Strahd") -> Session:
    return Session(
        id=3,
        poll_id=7,
        guild_id=1,
        channel_id=2,
        message_id=555,
        name=name,
        gm_id=GM,
        start=START,
        end=START + 4 * HOUR,
        roster=roster,
        cancelled=False,
    )


def field(embed: discord.Embed, name: str) -> str:
    values = [f.value for f in embed.fields if f.name == name]
    assert values, f"no field {name!r} in {[f.name for f in embed.fields]}"
    return str(values[0])


def field_names(embed: discord.Embed) -> list[str]:
    return [str(f.name) for f in embed.fields]


def timestamps(text: str, style: str) -> list[int]:
    return [int(v) for v in re.findall(rf"<t:(\d+):{style}>", text)]


def assert_within_limits(embed: discord.Embed) -> None:
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert len(embed.description or "") <= DESCRIPTION_LIMIT
    assert len(embed.fields) <= 25
    assert all(len(str(f.value)) <= FIELD_VALUE_LIMIT for f in embed.fields)


def no_mentions_in_code(embed: discord.Embed) -> None:
    texts = [embed.description or "", *(str(f.value) for f in embed.fields)]
    for text in texts:
        for block in re.findall(r"`[^`]*`", text):
            assert "<@" not in block


# build_poll_embed


def test_poll_embed_has_round_details_slots_and_footer() -> None:
    slots = make_slots(3)
    embed = build_poll_embed(
        make_poll(round_number=2, min_players=3),
        slots,
        [score(1, yes=2, if_needed=1), score(2, yes=1)],
        players=[GM, 101, 102],
        pending=[102],
    )

    description = embed.description or ""
    assert embed.title == "Curse of Strahd"
    assert "Round 2 of 6" in description
    assert f"<@{GM}>" in description
    assert "4 h" in description
    assert "at least 3 players" in description
    assert timestamps(description, "F") == [int(slot.start) for slot in slots]
    assert "✅ 2 · 🤔 1" in description
    # A slot without a score shows zeros.
    assert description.count("✅ 0 · 🤔 0") == 1
    assert embed.footer.text == content.POLL_FOOTER
    assert field(embed, content.JOINED_FIELD) == f"<@{GM}>\n<@101>\n<@102>"
    assert field(embed, content.WAITING_FIELD) == "<@102>"
    no_mentions_in_code(embed)


def test_poll_embed_optional_parts_omitted() -> None:
    embed = build_poll_embed(make_poll(), make_slots(1), [], players=[GM], pending=[])

    description = embed.description or ""
    assert "at least" not in description
    assert timestamps(description, "R") == []
    assert content.WAITING_FIELD not in field_names(embed)


def test_poll_embed_deadline_is_relative_timestamp() -> None:
    poll = make_poll(round_hours=48)
    embed = build_poll_embed(poll, make_slots(1), [], players=[GM], pending=[GM])

    assert timestamps(embed.description or "", "R") == [int(poll.round_started_at + 48 * HOUR)]


def test_poll_embed_escapes_name_and_keeps_long_names() -> None:
    embed = build_poll_embed(make_poll(name=EVIL_NAME), make_slots(1), [], [GM], [])
    assert embed.title is not None
    assert "](" not in embed.title
    assert "\\*\\*" in embed.title

    long_name = "x" * MAX_NAME_LENGTH
    embed = build_poll_embed(make_poll(name=long_name), make_slots(1), [], [GM], [])
    assert embed.title == long_name


def test_poll_embed_names_follow_mentions_escaped() -> None:
    embed = build_poll_embed(make_poll(), make_slots(1), [], [GM, 101], [], names={101: "**Bob**"})
    assert field(embed, content.JOINED_FIELD) == f"<@{GM}>\n<@101> \\*\\*Bob\\*\\*"


def test_poll_embed_within_limits_at_max_slots_and_players() -> None:
    players = [BIG_ID + i for i in range(MAX_PLAYERS)]
    slots = make_slots(MAX_SLOTS)
    embed = build_poll_embed(
        make_poll(name="W" * MAX_NAME_LENGTH, min_players=MAX_PLAYERS, round_hours=336),
        slots,
        [score(slot.id, yes=MAX_PLAYERS, if_needed=MAX_PLAYERS) for slot in slots],
        players=players,
        pending=players,
        names={player: "W" * 200 for player in players},
    )

    assert_within_limits(embed)
    assert timestamps(embed.description or "", "F") == [int(slot.start) for slot in slots]
    joined = "\n".join(
        str(f.value)
        for f in embed.fields
        if f.name in (content.JOINED_FIELD, content.JOINED_CONTINUED_FIELD)
    )
    assert all(f"<@{player}>" in joined for player in players)


def test_waiting_for_truncates_with_more() -> None:
    lines = [f"<@{BIG_ID + i}>" for i in range(60)]

    text = fit_with_more(lines)

    assert len(text) <= FIELD_VALUE_LIMIT
    shown = text.splitlines()
    assert shown[-1] == f"+{60 - (len(shown) - 1)} more"
    assert shown[:-1] == lines[: len(shown) - 1]


def test_fit_with_more_keeps_short_lists_whole() -> None:
    assert fit_with_more(["a", "b"]) == "a\nb"
    assert fit_with_more([]) == ""
    assert fit_with_more(["a"], hidden=2) == "a\n+2 more"


# Round results


def test_result_embed_lists_top_slots_and_who_cant_make_it() -> None:
    slots = make_slots(3)
    top = [score(2, yes=3), score(1, yes=2, if_needed=1)]
    votes = {
        (GM, 1): Vote.YES,
        (GM, 2): Vote.YES,
        (101, 1): Vote.IF_NEEDED,
        (101, 2): Vote.YES,
        (102, 1): Vote.NO,
        (102, 2): Vote.YES,
    }
    players = [GM, 101, 102, 103]

    embed = build_result_embed(make_poll(), top, slots, players, votes)

    description = embed.description or ""
    assert timestamps(description, "F") == [int(slots[1].start), int(slots[0].start)]
    assert "✅ 3 · 🤔 0" in description
    assert "✅ 2 · 🤔 1" in description
    lines = description.splitlines()
    cant = [line for line in lines if line.startswith("Can't make it")]
    assert cant == ["Can't make it: <@103>", "Can't make it: <@102>, <@103>"]
    assert f"<@{GM}>" in lines[0]
    assert embed.title == "Curse of Strahd"


def test_result_embed_everyone_can() -> None:
    votes = {(GM, 1): Vote.YES}
    embed = build_result_embed(make_poll(), [score(1, yes=1)], make_slots(1), [GM], votes)
    assert content.EVERYONE_CAN_LINE in (embed.description or "")


def test_next_round_embed() -> None:
    slots = make_slots(4)
    embed = build_next_round_embed(make_poll(round_number=3), slots, pending=[101, 102])

    description = embed.description or ""
    assert "round 3 of 6" in description
    assert timestamps(description, "d") == [int(slots[0].start), int(slots[-1].start)]
    assert field(embed, content.NEXT_ROUND_PENDING_FIELD) == "<@101>\n<@102>"


def test_no_fit_embed_gives_advice() -> None:
    embed = build_no_fit_embed(make_poll(round_number=6))
    description = embed.description or ""
    assert "6 rounds" in description
    assert content.NO_FIT_ADVICE in description
    for hint in ("fewer days", "more weeks", "min_players"):
        assert hint in description

    no_slots = build_no_fit_embed(make_poll(), FailReason.NO_SLOTS)
    assert content.NO_SLOTS_DESCRIPTION in (no_slots.description or "")


# Sessions


def test_session_embed() -> None:
    embed = build_session_embed(make_session(roster=(GM, 101, 102)))

    description = embed.description or ""
    assert timestamps(description, "F") == [int(START)]
    assert timestamps(description, "R") == [int(START)]
    assert "Curse of Strahd" in (embed.title or "")
    assert field(embed, content.SESSION_GM_FIELD) == f"<@{GM}>"
    assert field(embed, content.SESSION_LENGTH_FIELD) == "4 h"
    assert field(embed, content.SESSION_ROSTER_FIELD) == f"<@{GM}>\n<@101>\n<@102>"


def test_session_embed_escapes_name() -> None:
    embed = build_session_embed(make_session(name=EVIL_NAME))
    assert "](" not in (embed.title or "")


def test_session_embed_max_roster_within_limits() -> None:
    roster = tuple(BIG_ID + i for i in range(MAX_PLAYERS))
    embed = build_session_embed(make_session(roster=roster, name="W" * MAX_NAME_LENGTH))
    assert_within_limits(embed)


# /sessions list


def test_list_embed_empty_state() -> None:
    embed = build_list_embed([], [])
    assert embed.description == content.LIST_EMPTY_MESSAGE
    assert embed.fields == []


def test_list_embed_polls_and_sessions() -> None:
    voting = make_poll(round_number=2)
    confirming = make_poll(name="Other", status=STATUS_AWAITING_CONFIRM, message_id=None, poll_id=8)
    everyone = make_poll(name="Done voting", poll_id=9)

    embed = build_list_embed(
        [voting, confirming, everyone],
        [make_session()],
        pending={voting.id: [101, 102]},
    )

    polls = field(embed, content.LIST_POLLS_FIELD).splitlines()
    assert "https://discord.com/channels/1/2/555" in polls[0]
    assert "Round 2 of 6" in polls[0]
    assert "<@101>, <@102>" in polls[0]
    assert content.LIST_POLL_AWAITING_CONFIRM in polls[1]
    assert "https://" not in polls[1]
    assert content.LIST_POLL_ALL_VOTED in polls[2]
    sessions = field(embed, content.LIST_SESSIONS_FIELD)
    assert timestamps(sessions, "F") == [int(START)]
    assert timestamps(sessions, "R") == [int(START)]
    assert f"<@{GM}>" in sessions


def test_list_embed_caps_sessions() -> None:
    sessions = [make_session(name="W" * MAX_NAME_LENGTH) for _ in range(MAX_LIST_SESSIONS + 3)]

    embed = build_list_embed([], sessions)

    value = field(embed, content.LIST_SESSIONS_FIELD)
    assert len(value) <= FIELD_VALUE_LIMIT
    shown = value.splitlines()
    assert shown[-1] == f"+{len(sessions) - (len(shown) - 1)} more"
    assert_within_limits(embed)


def test_embed_module_reuses_the_one_escape() -> None:
    poll = make_poll(name="a_b")
    assert build_poll_embed(poll, [], [], [GM], []).title == "a\\_b"
