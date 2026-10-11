from __future__ import annotations

import ast
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ninja_bot.sessions import service
from ninja_bot.sessions.scoring import MAX_PLAYERS, Vote
from ninja_bot.sessions.service import (
    JOIN_MESSAGES,
    MAX_NAME_LENGTH,
    MAX_OPEN_POLLS,
    VOTE_MESSAGES,
    JoinResult,
    NoPrefill,
    Prefiller,
    SessionService,
    VoteResult,
)
from ninja_bot.sessions.slots import InvalidSessionInput
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_VOTING,
    Poll,
    PollSlot,
    SqliteSessionStore,
)

TZ = ZoneInfo("Europe/Amsterdam")
GUILD = 10
CHANNEL = 100
GM = 1
PLAYER = 2
OTHER = 3


class FakeClock:
    def __init__(self) -> None:
        # Monday 2026-11-02 12:00 UTC.
        self.now = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()

    def __call__(self) -> float:
        return self.now


class FixedPrefill:
    def __init__(self, vote: Vote, extra_ids: Sequence[int] = ()) -> None:
        self.vote = vote
        self.extra_ids = extra_ids
        self.calls: list[int] = []

    async def prefill(self, user_id: int, slots: Sequence[PollSlot]) -> dict[int, Vote]:
        self.calls.append(user_id)
        ids = [slot.id for slot in slots] + list(self.extra_ids)
        return dict.fromkeys(ids, self.vote)


def _service(tmp_path: Path, prefiller: Prefiller | None = None) -> SessionService:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    return SessionService(store, TZ, FakeClock(), prefiller or NoPrefill())


async def _create(svc: SessionService, guild_id: int = GUILD, **kwargs: object) -> Poll:
    args: dict[str, object] = {
        "guild_id": guild_id,
        "channel_id": CHANNEL,
        "gm_id": GM,
        "name": "Curse of Strahd",
        "days_text": "fri,sat",
        "times_text": "19:00",
    }
    args.update(kwargs)
    return await svc.create_poll(**args)  # type: ignore[arg-type]


async def _slot_ids(svc: SessionService, poll: Poll) -> list[int]:
    return [slot.id for slot in await svc.store.poll_slots(poll.id, poll.round_number)]


async def test_create_poll_uses_defaults(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc, name="  Curse of Strahd  ")
    assert poll.name == "Curse of Strahd"
    assert poll.status == STATUS_VOTING
    assert poll.round_number == 1
    assert poll.weeks == 2
    assert poll.duration_hours == 4.0
    assert poll.min_players == 0
    assert poll.round_hours == 0.0
    assert poll.window_start == date(2026, 11, 3)
    assert poll.days == (4, 5)
    slots = await svc.store.poll_slots(poll.id, 1)
    assert len(slots) == 4
    assert all(slot.end - slot.start == 4 * 3600 for slot in slots)
    assert await svc.store.players(poll.id) == [GM]


async def test_create_poll_keeps_options(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(
        svc,
        weeks=1,
        start_text="2026-11-09",
        duration_hours=3,
        min_players=3,
        round_hours=48,
    )
    assert (poll.weeks, poll.duration_hours, poll.min_players, poll.round_hours) == (1, 3, 3, 48)
    assert poll.window_start == date(2026, 11, 9)
    assert len(await svc.store.poll_slots(poll.id, 1)) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * (MAX_NAME_LENGTH + 1)},
        {"weeks": 0},
        {"weeks": 9},
        {"duration_hours": 0},
        {"duration_hours": 13},
        {"min_players": 1},
        {"min_players": MAX_PLAYERS + 1},
        {"round_hours": 0},
        {"round_hours": 337},
        {"days_text": "funday"},
        {"times_text": "7pm"},
        {"start_text": "2026-11-01"},
        {"start_text": "tomorrow"},
        {"days_text": "weekdays,weekend", "times_text": "9:00,14:00", "weeks": 2},
    ],
)
async def test_create_poll_rejects_invalid_input(tmp_path: Path, kwargs: dict[str, object]) -> None:
    svc = _service(tmp_path)
    with pytest.raises(InvalidSessionInput) as raised:
        await _create(svc, **kwargs)
    assert raised.value.message
    assert await svc.store.open_polls(GUILD) == []


async def test_create_poll_accepts_name_at_the_limit(tmp_path: Path) -> None:
    poll = await _create(_service(tmp_path), name="x" * MAX_NAME_LENGTH)
    assert len(poll.name) == MAX_NAME_LENGTH


async def test_open_poll_cap_is_per_guild(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    polls = [await _create(svc) for _ in range(MAX_OPEN_POLLS)]
    with pytest.raises(InvalidSessionInput):
        await _create(svc)
    await _create(svc, guild_id=GUILD + 1)
    await svc.store.cancel_poll(polls[0].id)
    await _create(svc)


async def test_join_and_leave_results(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    assert await svc.join(poll.id, PLAYER) is JoinResult.JOINED
    assert await svc.join(poll.id, PLAYER) is JoinResult.ALREADY_JOINED
    assert await svc.join(poll.id, GM) is JoinResult.ALREADY_JOINED
    assert await svc.leave(poll.id, OTHER) is JoinResult.NOT_JOINED
    assert await svc.leave(poll.id, GM) is JoinResult.GM_CANNOT_LEAVE
    assert await svc.leave(poll.id, PLAYER) is JoinResult.LEFT
    assert await svc.store.players(poll.id) == [GM]


async def test_join_full_poll(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    for user_id in range(100, 100 + MAX_PLAYERS - 1):
        assert await svc.join(poll.id, user_id) is JoinResult.JOINED
    assert await svc.join(poll.id, OTHER) is JoinResult.FULL


async def test_closed_or_unknown_poll(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    await svc.join(poll.id, PLAYER)
    await svc.store.set_status(poll.id, STATUS_AWAITING_CONFIRM)
    assert await svc.join(poll.id, OTHER) is JoinResult.POLL_CLOSED
    assert await svc.leave(poll.id, PLAYER) is JoinResult.POLL_CLOSED
    assert await svc.join(999, OTHER) is JoinResult.POLL_CLOSED
    slot_ids = await _slot_ids(svc, poll)
    assert await svc.submit_vote(poll.id, PLAYER, slot_ids, ()) is VoteResult.POLL_CLOSED
    assert await svc.submit_vote(999, PLAYER, (), ()) is VoteResult.POLL_CLOSED
    assert await svc.store.votes(poll.id, 1) == {}


async def test_submit_vote_records_every_slot(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    a, b, c, d = await _slot_ids(svc, poll)
    result = await svc.submit_vote(poll.id, GM, [a, b], [b, c])
    assert result is VoteResult.RECORDED
    assert await svc.store.votes(poll.id, 1) == {
        (GM, a): Vote.YES,
        (GM, b): Vote.YES,
        (GM, c): Vote.IF_NEEDED,
        (GM, d): Vote.NO,
    }


async def test_none_of_these_and_double_submission_replaces(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    slot_ids = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, slot_ids, ())
    assert await svc.submit_vote(poll.id, GM, (), ()) is VoteResult.RECORDED
    votes = await svc.store.votes(poll.id, 1)
    assert votes == {(GM, slot_id): Vote.NO for slot_id in slot_ids}


async def test_vote_by_non_member_is_refused(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    slot_ids = await _slot_ids(svc, poll)
    assert await svc.submit_vote(poll.id, OTHER, slot_ids, ()) is VoteResult.NOT_JOINED
    assert await svc.store.votes(poll.id, 1) == {}


async def test_vote_with_foreign_slot_is_refused(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    other = await _create(svc)
    (foreign, *_) = await _slot_ids(svc, other)
    (own, *_) = await _slot_ids(svc, poll)
    assert await svc.submit_vote(poll.id, GM, [own], [foreign]) is VoteResult.UNKNOWN_SLOT
    assert await svc.submit_vote(poll.id, GM, [12345], ()) is VoteResult.UNKNOWN_SLOT
    assert await svc.store.votes(poll.id, 1) == {}


async def test_no_prefill_records_nothing_on_join(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    await svc.join(poll.id, PLAYER)
    assert await svc.store.votes(poll.id, 1) == {}


async def test_prefill_applies_on_join_for_current_slots_only(tmp_path: Path) -> None:
    prefill = FixedPrefill(Vote.IF_NEEDED, extra_ids=[12345])
    svc = _service(tmp_path, prefill)
    poll = await _create(svc)
    slot_ids = await _slot_ids(svc, poll)
    assert await svc.join(poll.id, PLAYER) is JoinResult.JOINED
    assert prefill.calls == [PLAYER]
    votes = await svc.store.votes(poll.id, 1)
    assert votes == {(PLAYER, slot_id): Vote.IF_NEEDED for slot_id in slot_ids}
    # Explicit votes replace the prefill, and a repeated join doesn't prefill again.
    await svc.submit_vote(poll.id, PLAYER, slot_ids[:1], ())
    assert await svc.join(poll.id, PLAYER) is JoinResult.ALREADY_JOINED
    assert prefill.calls == [PLAYER]
    assert (await svc.store.votes(poll.id, 1))[(PLAYER, slot_ids[0])] is Vote.YES


async def test_prefill_never_overwrites_an_explicit_vote(tmp_path: Path) -> None:
    prefill = FixedPrefill(Vote.YES)
    svc = _service(tmp_path, prefill)
    poll = await _create(svc)
    first, *rest = await _slot_ids(svc, poll)
    # A vote stored before the join (e.g. a racing click) is kept.
    await svc.store.record_votes(poll.id, PLAYER, {first: Vote.NO})
    await svc.join(poll.id, PLAYER)
    votes = await svc.store.votes(poll.id, 1)
    assert votes[(PLAYER, first)] is Vote.NO
    assert all(votes[(PLAYER, slot_id)] is Vote.YES for slot_id in rest)


def test_every_result_has_a_message() -> None:
    assert set(JOIN_MESSAGES) == set(JoinResult)
    assert set(VOTE_MESSAGES) == set(VoteResult)
    assert all(JOIN_MESSAGES.values()) and all(VOTE_MESSAGES.values())


@pytest.mark.parametrize("module", ["service", "content"])
def test_modules_do_not_import_discord(module: str) -> None:
    path = Path(service.__file__).with_name(f"{module}.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    ]
    assert not any(name.split(".")[0] == "discord" for name in modules)
    if module == "content":
        assert not any(name.split(".")[0] == "ninja_bot" for name in modules)
