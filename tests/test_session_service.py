from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ninja_bot.sessions import service
from ninja_bot.sessions.scoring import MAX_PLAYERS, MAX_ROUNDS, Vote
from ninja_bot.sessions.service import (
    JOIN_MESSAGES,
    MAX_NAME_LENGTH,
    MAX_OPEN_POLLS,
    VOTE_MESSAGES,
    FailReason,
    JoinResult,
    NoPrefill,
    NotAllowed,
    NotVoting,
    Prefiller,
    RoundAdvanced,
    RoundFailed,
    RoundFits,
    SessionRefused,
    SessionService,
    VoteResult,
    should_auto_close,
)
from ninja_bot.sessions.slots import InvalidSessionInput
from ninja_bot.sessions.store import (
    STATUS_AWAITING_CONFIRM,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_FAILED,
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


async def _three_players(svc: SessionService) -> tuple[Poll, list[int]]:
    poll = await _create(svc)
    await svc.join(poll.id, PLAYER)
    await svc.join(poll.id, OTHER)
    return poll, await _slot_ids(svc, poll)


async def test_close_round_fits(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll, (a, b, c, _d) = await _three_players(svc)
    await svc.submit_vote(poll.id, GM, [a, b, c], ())
    await svc.submit_vote(poll.id, PLAYER, [b, c], [a])
    await svc.submit_vote(poll.id, OTHER, [c], [a, b])
    outcome = await svc.close_round(poll.id, GM, False, "manual")
    assert isinstance(outcome, RoundFits)
    assert outcome.poll.status == STATUS_AWAITING_CONFIRM
    assert [score.slot_id for score in outcome.top] == [c, b, a]
    assert [slot.id for slot in outcome.slots] == [c, b, a]
    assert outcome.pending == ()


async def test_close_round_advances_to_next_window(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll, slot_ids = await _three_players(svc)
    await svc.submit_vote(poll.id, GM, slot_ids, ())
    await svc.submit_vote(poll.id, PLAYER, (), ())
    outcome = await svc.close_round(poll.id, 0, False, "deadline")
    assert isinstance(outcome, RoundAdvanced)
    assert outcome.poll.status == STATUS_VOTING
    assert outcome.poll.round_number == 2
    assert outcome.poll.window_start == date(2026, 11, 17)
    assert outcome.poll.round_started_at == svc.clock()
    assert outcome.pending == (OTHER,)
    assert len(outcome.slots) == 4
    assert outcome.slots == tuple(await svc.store.poll_slots(poll.id, 2))
    first = datetime.fromtimestamp(outcome.slots[0].start, TZ)
    assert (first.date(), first.hour) == (date(2026, 11, 20), 19)


async def test_last_round_without_fit_fails(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    for round_number in range(1, MAX_ROUNDS):
        outcome = await svc.close_round(poll.id, GM, False, "manual")
        assert isinstance(outcome, RoundAdvanced)
        assert outcome.poll.round_number == round_number + 1
    outcome = await svc.close_round(poll.id, GM, False, "manual")
    assert isinstance(outcome, RoundFailed)
    assert outcome.reason is FailReason.LAST_ROUND
    assert outcome.poll.status == STATUS_FAILED
    assert outcome.pending == (GM,)


async def test_next_window_without_slots_fails(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.clock.now = datetime(2027, 3, 1, 12, tzinfo=UTC).timestamp()  # type: ignore[attr-defined]
    # The next window's only Sunday 02:30 is in the 2027-03-28 spring-forward gap.
    poll = await _create(svc, days_text="sun", times_text="02:30", weeks=1, start_text="2027-03-21")
    outcome = await svc.close_round(poll.id, GM, False, "manual")
    assert isinstance(outcome, RoundFailed)
    assert outcome.reason is FailReason.NO_SLOTS
    assert outcome.poll.status == STATUS_FAILED
    assert outcome.poll.round_number == 1


async def test_double_close_is_harmless(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll, slot_ids = await _three_players(svc)
    for user in (GM, PLAYER, OTHER):
        await svc.submit_vote(poll.id, user, slot_ids, ())
    first, second = await asyncio.gather(
        svc.close_round(poll.id, GM, False, "manual"),
        svc.close_round(poll.id, 0, False, "all_voted"),
    )
    assert isinstance(first, RoundFits)
    assert isinstance(second, NotVoting)
    assert second.poll is not None and second.poll.status == STATUS_AWAITING_CONFIRM
    assert isinstance(await svc.close_round(999, GM, True, "manual"), NotVoting)


async def test_manual_close_permissions(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll, _ = await _three_players(svc)
    refused = await svc.close_round(poll.id, PLAYER, False, "manual")
    assert isinstance(refused, NotAllowed)
    assert (await svc.store.get_poll(poll.id)).status == STATUS_VOTING  # type: ignore[union-attr]
    assert isinstance(await svc.close_round(poll.id, PLAYER, True, "manual"), RoundAdvanced)
    # Automatic closes skip the check.
    assert isinstance(await svc.close_round(poll.id, PLAYER, False, "deadline"), RoundAdvanced)


async def test_confirm_builds_roster_from_yes_and_if_needed(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc, min_players=2)
    await svc.join(poll.id, PLAYER)
    await svc.join(poll.id, OTHER)
    a, b, _c, _d = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a], [b])
    await svc.submit_vote(poll.id, PLAYER, (), [b])
    await svc.submit_vote(poll.id, OTHER, [a, b], ())
    outcome = await svc.close_round(poll.id, GM, False, "manual")
    assert isinstance(outcome, RoundFits)
    session = await svc.confirm(poll.id, b, GM, False)
    assert session.roster == (GM, PLAYER, OTHER)
    assert session.start == outcome.slots[[s.slot_id for s in outcome.top].index(b)].start
    assert await svc.store.get_session(session.id) == session
    assert (await svc.store.get_poll(poll.id)).status == STATUS_CONFIRMED  # type: ignore[union-attr]


async def test_confirm_roster_skips_no_votes(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc, min_players=2)
    await svc.join(poll.id, PLAYER)
    await svc.join(poll.id, OTHER)
    a, *_ = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a], ())
    await svc.submit_vote(poll.id, PLAYER, (), [a])
    await svc.submit_vote(poll.id, OTHER, (), ())
    assert isinstance(await svc.close_round(poll.id, GM, False, "manual"), RoundFits)
    session = await svc.confirm(poll.id, a, OTHER, True)
    assert session.roster == (GM, PLAYER)


async def test_confirm_refusals(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll, (a, b, _c, _d) = await _three_players(svc)
    with pytest.raises(SessionRefused) as voting:
        await svc.confirm(poll.id, a, GM, False)
    assert voting.value.message == service.content.NOT_AWAITING_CONFIRM_MESSAGE
    for user in (GM, PLAYER, OTHER):
        await svc.submit_vote(poll.id, user, [a], [b] if user != OTHER else ())
    assert isinstance(await svc.close_round(poll.id, GM, False, "manual"), RoundFits)
    with pytest.raises(SessionRefused) as not_offered:
        await svc.confirm(poll.id, b, GM, False)
    assert not_offered.value.message == service.content.SLOT_NOT_OFFERED_MESSAGE
    with pytest.raises(SessionRefused) as not_gm:
        await svc.confirm(poll.id, a, PLAYER, False)
    assert not_gm.value.message == service.content.NOT_ALLOWED_MESSAGE
    with pytest.raises(SessionRefused) as unknown:
        await svc.confirm(999, a, GM, True)
    assert unknown.value.message == service.content.UNKNOWN_POLL_MESSAGE
    await svc.confirm(poll.id, a, GM, False)
    # A second click finds the poll confirmed.
    with pytest.raises(SessionRefused):
        await svc.confirm(poll.id, a, GM, False)
    assert len(await svc.store.upcoming_sessions(GUILD, svc.clock())) == 1


async def test_cancel_poll_permissions(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    with pytest.raises(SessionRefused) as not_gm:
        await svc.cancel_poll(poll.id, PLAYER, False)
    assert not_gm.value.message == service.content.NOT_ALLOWED_MESSAGE
    cancelled = await svc.cancel_poll(poll.id, PLAYER, True)
    assert cancelled.status == STATUS_CANCELLED
    for poll_id in (poll.id, 999):
        with pytest.raises(SessionRefused) as gone:
            await svc.cancel_poll(poll_id, GM, False)
        assert gone.value.message == service.content.UNKNOWN_POLL_MESSAGE
    other = await _create(svc)
    assert (await svc.cancel_poll(other.id, GM, False)).status == STATUS_CANCELLED


async def test_cancel_session_permissions(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    a, *_ = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a], ())
    assert isinstance(await svc.close_round(poll.id, GM, False, "all_voted"), RoundFits)
    session = await svc.confirm(poll.id, a, GM, False)
    with pytest.raises(SessionRefused) as not_gm:
        await svc.cancel_session(session.id, PLAYER, False)
    assert not_gm.value.message == service.content.NOT_ALLOWED_MESSAGE
    cancelled = await svc.cancel_session(session.id, PLAYER, True)
    assert cancelled.cancelled
    assert (await svc.store.get_session(session.id)).cancelled  # type: ignore[union-attr]
    for session_id in (session.id, 999):
        with pytest.raises(SessionRefused) as gone:
            await svc.cancel_session(session_id, GM, False)
        assert gone.value.message == service.content.UNKNOWN_SESSION_MESSAGE


async def test_cancel_started_session_is_refused(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    a, *_ = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a], ())
    await svc.close_round(poll.id, GM, False, "all_voted")
    session = await svc.confirm(poll.id, a, GM, False)
    svc.clock.now = session.start  # type: ignore[attr-defined]
    with pytest.raises(SessionRefused):
        await svc.cancel_session(session.id, GM, False)


async def test_should_auto_close_reasons(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc, round_hours=24)
    await svc.join(poll.id, PLAYER)
    slot_ids = await _slot_ids(svc, poll)
    players = await svc.store.players(poll.id)
    start = poll.round_started_at
    assert should_auto_close(poll, players, slot_ids, {}, start) is None
    assert should_auto_close(poll, players, slot_ids, {}, start + 86_399) is None
    assert should_auto_close(poll, players, slot_ids, {}, start + 86_400) == "deadline"
    votes = {(GM, slot_ids[0]): Vote.YES, (PLAYER, slot_ids[1]): Vote.NO}
    assert should_auto_close(poll, players, slot_ids, votes, start) == "all_voted"
    assert should_auto_close(poll, players, slot_ids, votes, start + 86_400) == "all_voted"
    no_deadline = await _create(svc)
    assert should_auto_close(no_deadline, [GM], slot_ids, {}, start + 10**7) is None
    closed = replace(poll, status=STATUS_AWAITING_CONFIRM)
    assert should_auto_close(closed, players, slot_ids, votes, start + 86_400) is None


async def test_lone_gm_vote_does_not_auto_close(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc, round_hours=24)
    a, *_ = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a], ())
    players = await svc.store.players(poll.id)
    assert players == [GM]
    votes = await svc.store.votes(poll.id, poll.round_number)
    start = poll.round_started_at
    assert should_auto_close(poll, players, [a], votes, start) is None
    assert should_auto_close(poll, players, [a], votes, start + 86_400) == "deadline"
    assert isinstance(await svc.close_round(poll.id, GM, False, "manual"), RoundFits)


class GatedStore(SqliteSessionStore):
    """Holds `record_votes` until `release` is set, so a vote can be caught mid-write."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def record_votes(self, poll_id: int, user_id: int, votes: Mapping[int, Vote]) -> None:
        self.entered.set()
        await self.release.wait()
        await super().record_votes(poll_id, user_id, votes)


def _gated_service(tmp_path: Path) -> tuple[SessionService, GatedStore]:
    store = GatedStore(tmp_path / "sessions.sqlite3")
    return SessionService(store, TZ, FakeClock(), NoPrefill()), store


async def test_concurrent_joins_by_the_same_user(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    results = await asyncio.gather(svc.join(poll.id, PLAYER), svc.join(poll.id, PLAYER))
    assert sorted(result.value for result in results) == sorted(
        [JoinResult.JOINED.value, JoinResult.ALREADY_JOINED.value]
    )
    assert await svc.store.players(poll.id) == [GM, PLAYER]


async def test_vote_in_flight_is_counted_by_a_concurrent_close(tmp_path: Path) -> None:
    svc, store = _gated_service(tmp_path)
    poll = await _create(svc)
    await svc.join(poll.id, PLAYER)
    a, *_ = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a], ())
    store.entered.clear()
    store.release.clear()
    vote = asyncio.create_task(svc.submit_vote(poll.id, PLAYER, [a], ()))
    await store.entered.wait()
    close = asyncio.create_task(svc.close_round(poll.id, GM, False, "manual"))
    # Unserialised, the close would evaluate the round without the held vote by now.
    await asyncio.wait({close}, timeout=0.2)
    assert not close.done()
    store.release.set()
    assert await vote == VoteResult.RECORDED
    outcome = await close
    assert isinstance(outcome, RoundFits)
    offered = [slot.id for slot in outcome.slots]
    assert offered == [a]
    session = await svc.confirm(poll.id, a, GM, False)
    assert session.roster == (GM, PLAYER)


async def test_vote_after_a_close_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    svc, store = _gated_service(tmp_path)
    poll = await _create(svc)
    await svc.join(poll.id, PLAYER)
    a, b, *_ = await _slot_ids(svc, poll)
    await svc.submit_vote(poll.id, GM, [a, b], ())
    await svc.submit_vote(poll.id, PLAYER, [a], ())
    close = asyncio.create_task(svc.close_round(poll.id, GM, False, "manual"))
    await asyncio.sleep(0)
    vote = asyncio.create_task(svc.submit_vote(poll.id, PLAYER, [b], ()))
    outcome = await close
    assert await vote == VoteResult.POLL_CLOSED
    assert isinstance(outcome, RoundFits)
    assert [slot.id for slot in outcome.slots] == [a]
    votes = await svc.store.votes(poll.id, poll.round_number)
    assert votes[(PLAYER, b)] == Vote.NO
    with pytest.raises(SessionRefused):
        await svc.confirm(poll.id, b, GM, False)
    session = await svc.confirm(poll.id, a, GM, False)
    assert session.start == outcome.slots[0].start


async def test_join_after_a_close_is_refused(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    poll = await _create(svc)
    await svc.submit_vote(poll.id, GM, await _slot_ids(svc, poll), ())
    close = asyncio.create_task(svc.close_round(poll.id, GM, False, "manual"))
    await asyncio.sleep(0)
    join = asyncio.create_task(svc.join(poll.id, PLAYER))
    assert isinstance(await close, RoundFits)
    assert await join == JoinResult.POLL_CLOSED
    assert await svc.store.players(poll.id) == [GM]
