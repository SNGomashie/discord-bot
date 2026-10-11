"""/activity and /myactivity: wiring, month handling, cooldown and replies."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ninja_bot.activity.embed import EMPTY_MONTH_MESSAGE, POKEMON_FIELD, SHAME_FIELD
from ninja_bot.activity.periods import FUTURE_MONTH_MESSAGE, INVALID_MONTH_MESSAGE, Month
from ninja_bot.activity.store import PokemonEvent, Session
from ninja_bot.cogs.activity import (
    COOLDOWN_SECONDS,
    GENERIC_ERROR_MESSAGE,
    Activity,
    cooldown_message,
    no_activity_message,
)
from ninja_bot.media.cooldown import UserCooldown

USER_ID = 42
OTHER_ID = 7
# 2026-10-15 12:00 UTC: "this month" is October 2026.
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC).timestamp()


class FakeClock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeStore:
    """Only what the commands read; `sessions_between` mirrors the SQL window."""

    def __init__(
        self,
        sessions: list[Session] | None = None,
        fail: bool = False,
        events: list[PokemonEvent] | None = None,
    ) -> None:
        self.sessions = sessions or []
        self.events = events or []
        self.fail = fail
        self.windows: list[tuple[float, float]] = []

    async def sessions_between(self, start_ts: float, end_ts: float) -> list[Session]:
        if self.fail:
            raise RuntimeError("database is locked")
        self.windows.append((start_ts, end_ts))
        return [
            s
            for s in self.sessions
            if s.start_ts < end_ts and (s.end_ts is None or s.end_ts > start_ts)
        ]

    async def pokemon_events_between(self, start_ts: float, end_ts: float) -> list[PokemonEvent]:
        return [e for e in self.events if start_ts <= e.ts < end_ts]

    async def voice_user_ranges(self) -> dict[int, tuple[float, float]]:
        ranges: dict[int, tuple[float, float]] = {}
        for s in self.sessions:
            first, last = ranges.get(s.user_id, (s.start_ts, s.start_ts))
            ranges[s.user_id] = (min(first, s.start_ts), max(last, s.start_ts))
        return ranges


def ts(day: int, hour: int, month: int = 10) -> float:
    return datetime(2026, month, day, hour, tzinfo=UTC).timestamp()


def session(user_id: int, start: float, hours: float, session_id: int = 1) -> Session:
    end = start + hours * 3600
    return Session(session_id, user_id, 10, start, end, end)


def make_cog(store: Any, clock: FakeClock | None = None) -> Activity:
    clock = clock or FakeClock()
    return Activity(
        MagicMock(), store, 1, clock=clock, cooldown=UserCooldown(COOLDOWN_SECONDS, clock)
    )


def make_interaction(user_id: int = USER_ID) -> MagicMock:
    interaction = MagicMock()
    interaction.guild = None
    interaction.user.id = user_id
    interaction.user.display_name = "Caller"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_member(user_id: int, name: str) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.display_name = name
    return member


def reply(interaction: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    return args, kwargs


def busy_store() -> FakeStore:
    return FakeStore(
        [
            session(USER_ID, ts(1, 18), 3, 1),
            session(OTHER_ID, ts(2, 18), 1, 2),
            session(USER_ID, ts(20, 18, month=9), 2, 3),
        ]
    )


def assert_public_embed(interaction: MagicMock) -> discord.Embed:
    _, kwargs = reply(interaction)
    assert not kwargs.get("ephemeral")
    assert kwargs["allowed_mentions"].users is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    return embed


async def test_activity_posts_the_current_month_publicly() -> None:
    store = busy_store()
    interaction = make_interaction()
    await make_cog(store).handle_activity(interaction, None)
    embed = assert_public_embed(interaction)
    assert "October 2026" in (embed.title or "")
    assert f"<@{USER_ID}>" in embed.fields[0].value
    # This month and the month before (for climber/drop awards).
    assert store.windows == [
        (ts(1, 0), datetime(2026, 11, 1, tzinfo=UTC).timestamp()),
        (ts(1, 0, month=9), ts(1, 0)),
    ]


async def test_activity_takes_an_explicit_month() -> None:
    interaction = make_interaction()
    await make_cog(busy_store()).handle_activity(interaction, " 2026-09 ")
    embed = assert_public_embed(interaction)
    assert "September 2026" in (embed.title or "")


async def test_activity_uses_the_configured_timezone() -> None:
    # 2026-10-31 23:30 UTC is already November in Amsterdam (UTC+1 after DST ends).
    clock = FakeClock(datetime(2026, 10, 31, 23, 30, tzinfo=UTC).timestamp())
    cog = Activity(MagicMock(), FakeStore(), 1, clock=clock, timezone="Europe/Amsterdam")
    interaction = make_interaction()
    await cog.handle_activity(interaction, None)
    assert "November 2026" in (assert_public_embed(interaction).title or "")


@pytest.mark.parametrize(
    ("month", "message"),
    [
        ("september", INVALID_MONTH_MESSAGE),
        ("2026-13", INVALID_MONTH_MESSAGE),
        ("2015-13", INVALID_MONTH_MESSAGE),
        ("0001-01", INVALID_MONTH_MESSAGE),
        ("2014-12", INVALID_MONTH_MESSAGE),
        ("9999-12", INVALID_MONTH_MESSAGE),
        ("2026-11", FUTURE_MONTH_MESSAGE),
        ("2030-01", FUTURE_MONTH_MESSAGE),
    ],
)
async def test_bad_or_future_month_is_a_private_error(month: str, message: str) -> None:
    store = busy_store()
    for handle in ("activity", "myactivity"):
        cog = make_cog(store)
        interaction = make_interaction()
        if handle == "activity":
            await cog.handle_activity(interaction, month)
        else:
            await cog.handle_myactivity(interaction, None, month)
        assert reply(interaction) == ((message,), {"ephemeral": True})
        # Invalid input never takes the cooldown.
        assert cog.cooldown.try_acquire(USER_ID) == 0.0
    assert store.windows == []


@pytest.mark.parametrize("timezone", ["UTC", "Pacific/Kiritimati", "Pacific/Pago_Pago"])
async def test_earliest_month_builds_a_report(timezone: str) -> None:
    sessions = [session(USER_ID, datetime(2015, 1, 10, 18, tzinfo=UTC).timestamp(), 2)]
    store = FakeStore(sessions)
    cog = Activity(MagicMock(), store, 1, clock=FakeClock(), timezone=timezone)
    interaction = make_interaction()
    await cog.handle_activity(interaction, "2015-01")
    assert "January 2015" in (assert_public_embed(interaction).title or "")

    # A fresh cog: the first post took the shared cooldown.
    cog = Activity(MagicMock(), store, 1, clock=FakeClock(), timezone=timezone)
    mine = make_interaction()
    await cog.handle_myactivity(mine, None, "2015-01")
    assert assert_public_embed(mine).title == "Caller · January 2015"


async def test_empty_month_posts_the_empty_state_and_refunds() -> None:
    cog = make_cog(FakeStore())
    interaction = make_interaction()
    await cog.handle_activity(interaction, None)
    embed = assert_public_embed(interaction)
    assert embed.description == EMPTY_MONTH_MESSAGE.format(month="October 2026")
    assert cog.cooldown.try_acquire(USER_ID) == 0.0


async def test_cooldown_is_shared_and_blocks_a_second_request() -> None:
    clock = FakeClock()
    cog = make_cog(busy_store(), clock)
    await cog.handle_activity(make_interaction(), None)

    blocked = make_interaction()
    clock.now += 4
    await cog.handle_myactivity(blocked, None, None)
    assert reply(blocked) == ((cooldown_message(6),), {"ephemeral": True})

    # Other members have their own cooldown.
    other = make_interaction(OTHER_ID)
    await cog.handle_myactivity(other, None, None)
    assert_public_embed(other)

    clock.now += 6
    again = make_interaction()
    await cog.handle_activity(again, None)
    assert_public_embed(again)


def test_cooldown_message_rounds_up() -> None:
    assert cooldown_message(0.2) == ("Slow down! You can use activity commands again in 1 second.")
    assert cooldown_message(9.1).endswith("in 10 seconds.")


async def test_store_errors_get_the_generic_reply_and_refund(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for handle in ("activity", "myactivity"):
        cog = make_cog(FakeStore(fail=True))
        interaction = make_interaction()
        if handle == "activity":
            await cog.handle_activity(interaction, None)
        else:
            await cog.handle_myactivity(interaction, None, None)
        assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
        assert cog.cooldown.try_acquire(USER_ID) == 0.0
    assert "Unexpected error" in caplog.text


async def test_failed_send_refunds() -> None:
    cog = make_cog(busy_store())
    interaction = make_interaction()
    interaction.response.send_message.side_effect = discord.HTTPException(
        MagicMock(status=500), "boom"
    )
    await cog.handle_activity(interaction, None)
    assert cog.cooldown.try_acquire(USER_ID) == 0.0


async def test_myactivity_defaults_to_the_caller() -> None:
    interaction = make_interaction()
    await make_cog(busy_store()).handle_myactivity(interaction, None, None)
    embed = assert_public_embed(interaction)
    assert embed.title == "Caller · October 2026"


async def test_myactivity_for_another_member_and_month() -> None:
    interaction = make_interaction()
    cog = make_cog(busy_store())
    await cog.handle_myactivity(interaction, make_member(OTHER_ID, "Friend"), "2026-10")
    embed = assert_public_embed(interaction)
    assert embed.title == "Friend · October 2026"
    assert cog.cooldown.try_acquire(USER_ID) > 0


async def test_myactivity_without_data_is_a_private_error_and_refunds() -> None:
    cog = make_cog(busy_store())
    own = make_interaction(99)
    await cog.handle_myactivity(own, None, None)
    assert reply(own) == (
        (no_activity_message(None, Month(2026, 10)),),
        {"ephemeral": True},
    )
    assert cog.cooldown.try_acquire(99) == 0.0

    other = make_interaction()
    await make_cog(busy_store()).handle_myactivity(
        other, make_member(OTHER_ID, "Fr*end"), "2026-09"
    )
    assert reply(other) == (
        ("**Fr\\*end** wasn't in voice in September 2026.",),
        {"ephemeral": True},
    )


def test_no_activity_message_for_the_caller() -> None:
    assert no_activity_message(None, Month(2026, 9)) == "You weren't in voice in September 2026."


def test_commands_and_parameters_have_descriptions() -> None:
    for command in (Activity.activity, Activity.myactivity):
        assert command.description
        for param in command.parameters:
            assert str(param.description).strip()
            assert not param.required


async def test_pokemon_data_shows_in_both_commands() -> None:
    store = busy_store()
    store.events = [
        PokemonEvent(1, USER_ID, ts(3, 12), "catch", "Pikachu", True),
        PokemonEvent(2, USER_ID, ts(3, 13), "cooldown"),
        PokemonEvent(3, USER_ID, ts(3, 12, month=9), "catch", "Mew"),
    ]
    interaction = make_interaction()
    cog = make_cog(store)
    await cog.handle_activity(interaction, None)
    embed = assert_public_embed(interaction)
    pokemon = [f.value for f in embed.fields if f.name == POKEMON_FIELD]
    assert pokemon and "shiny Pikachu" in pokemon[0] and "Mew" not in pokemon[0]

    own = make_interaction(OTHER_ID)
    await cog.handle_myactivity(own, make_member(USER_ID, "Ash"), None)
    fields = {f.name: f.value for f in assert_public_embed(own).fields}
    assert fields[POKEMON_FIELD] == "1 catch · 1 shiny · 1 cooldown hit"


async def test_month_with_only_pokemon_keeps_the_cooldown() -> None:
    store = FakeStore(events=[PokemonEvent(1, USER_ID, ts(3, 12), "catch", "Eevee")])
    cog = make_cog(store)
    interaction = make_interaction()
    await cog.handle_activity(interaction, None)
    embed = assert_public_embed(interaction)
    assert embed.description == EMPTY_MONTH_MESSAGE.format(month="October 2026")
    assert POKEMON_FIELD in [f.name for f in embed.fields]
    assert cog.cooldown.try_acquire(USER_ID) > 0


class FakeGuild:
    """Nobody cached; `fetch_member` raises NotFound for `departed`, a 503 for `failing`."""

    def __init__(self, departed: Iterable[int] = (), failing: Iterable[int] = ()) -> None:
        self.id = 1
        self.departed = set(departed)
        self.failing = set(failing)
        self.fetch_calls: list[int] = []

    def get_member(self, user_id: int) -> discord.Member | None:
        return None

    async def fetch_member(self, user_id: int) -> MagicMock:
        self.fetch_calls.append(user_id)
        if user_id in self.departed:
            raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
        if user_id in self.failing:
            raise discord.HTTPException(MagicMock(status=503, reason="Unavailable"), "down")
        return make_member(user_id, f"Member {user_id}")


def shame_store() -> FakeStore:
    """Five members in October; 1 has the least time, 5 the most."""
    return FakeStore([session(u, ts(u, 18), u, u) for u in range(1, 6)])


def shame_lines(interaction: MagicMock) -> list[str]:
    embed = assert_public_embed(interaction)
    return next(f.value for f in embed.fields if f.name == SHAME_FIELD).splitlines()


async def test_activity_skips_departed_members_and_refills_the_shame_list() -> None:
    guild = FakeGuild(departed={2})
    interaction = make_interaction()
    interaction.guild = guild
    await make_cog(shame_store()).handle_activity(interaction, None)

    lines = shame_lines(interaction)
    assert [line.split()[1] for line in lines] == ["<@1>", "<@3>", "<@4>"]
    assert guild.fetch_calls == [1, 2, 3, 4]  # stops once the list is full


async def test_lookup_error_keeps_the_candidate_and_is_not_cached() -> None:
    guild = FakeGuild(failing={1})
    cog = make_cog(shame_store())
    interaction = make_interaction()
    interaction.guild = guild
    await cog.handle_activity(interaction, None)

    assert [line.split()[1] for line in shame_lines(interaction)] == ["<@1>", "<@2>", "<@3>"]
    report = await cog.monthly_report(Month(2026, 10), guild)  # type: ignore[arg-type]
    assert [user_id for user_id, _ in report.shame] == [1, 2, 3]
    assert guild.fetch_calls.count(1) == 2  # the error was retried, not cached


async def test_all_shame_candidates_departed_leaves_no_shame_field() -> None:
    interaction = make_interaction()
    interaction.guild = FakeGuild(departed={1, 2, 3, 4, 5})
    await make_cog(shame_store()).handle_activity(interaction, None)

    embed = assert_public_embed(interaction)
    assert SHAME_FIELD not in [f.name for f in embed.fields]
