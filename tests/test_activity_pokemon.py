from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from ninja_bot.activity.pokemon import (
    Catch,
    Cooldown,
    PendingCommands,
    is_pokemon_command,
    parse_duration,
    parse_reply,
    to_event,
)
from ninja_bot.activity.store import PokemonEvent, SqliteActivityStore
from ninja_bot.cogs.activity import PokemonTracker

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "toasty" / "replies.json").read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = FIXTURES["cases"]
TOASTY_CASES = [case for case in CASES if case["from_toasty"]]

GUILD_ID = 1
TOASTY = 500
OTHER_BOT = 501
USER = 7
REPLY_TIME = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def expected_result(expected: dict[str, Any] | None) -> Catch | Cooldown | None:
    if expected is None:
        return None
    if expected["kind"] == "catch":
        return Catch(expected["pokemon"], expected["shiny"])
    return Cooldown(expected["wait_seconds"])


@pytest.mark.parametrize(
    ("content", "expected"),
    [(invoker["content"], invoker["is_command"]) for invoker in FIXTURES["invokers"]]
    + [
        ("  ;pokemon  ", True),
        (";  pokemon", False),
        ("?pokemon", False),
        (";pokemon\nlist", False),
        (";pokémon", False),
        ("", False),
    ],
)
def test_is_pokemon_command(content: str, expected: bool) -> None:
    assert is_pokemon_command(content) is expected


@pytest.mark.parametrize("case", TOASTY_CASES, ids=[case["name"] for case in TOASTY_CASES])
def test_parse_reply_fixtures(case: dict[str, Any]) -> None:
    result = parse_reply(case["reply_content"], case["reply_embeds"])

    assert result == expected_result(case["expected"])


def test_fixtures_cover_the_required_cases() -> None:
    names = {case["name"] for case in CASES}
    assert {
        "catch_plain",
        "catch_article_an",
        "catch_trailing_char",
        "shiny_all_in_content",
        "shiny_text_in_embed",
        "cooldown",
        "non_toasty_reply",
    } <= names
    assert all(isinstance(case["assumed"], bool) for case in CASES)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1h 50m 16s", 6616),
        ("5m 3s", 303),
        ("16s", 16),
        ("2h", 7200),
        ("1d 2h", 93600),
        ("1h50m", 6600),
        ("a bit", None),
        ("", None),
        ("5 minutes", None),
    ],
)
def test_parse_duration(text: str, expected: int | None) -> None:
    assert parse_duration(text) == expected


def test_parse_reply_ignores_non_string_embed_parts() -> None:
    embeds: list[dict[str, Any]] = [{"title": None, "fields": [{"name": "x"}, "junk"]}]

    assert parse_reply("", embeds) is None


def test_parse_reply_keeps_names_with_spaces_and_symbols() -> None:
    assert parse_reply("<@1>, you've caught a Type: Null!", []) == Catch("Type: Null", False)
    assert parse_reply("<@1>, you’ve caught a Ho-Oh!", []) == Catch("Ho-Oh", False)
    assert parse_reply("<@1>, you've caught a shiny Porygon2!", []) == Catch("Porygon2", True)


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_pending_commands_expire_after_ttl() -> None:
    clock = FakeClock()
    pending = PendingCommands(clock, ttl_seconds=300.0)
    pending.remember(1, USER)

    clock.now = 299.0
    assert pending.author_of(1) == USER
    clock.now = 300.0
    assert pending.author_of(1) is None
    assert len(pending) == 0


def test_pending_commands_are_capped_oldest_first() -> None:
    pending = PendingCommands(FakeClock(), max_size=2)
    pending.remember(1, 10)
    pending.remember(2, 20)
    pending.remember(3, 30)

    assert pending.author_of(1) is None
    assert (pending.author_of(2), pending.author_of(3)) == (20, 30)


def test_pending_lookup_does_not_consume() -> None:
    pending = PendingCommands(FakeClock())
    pending.remember(1, USER)

    assert pending.author_of(1) == USER
    assert pending.author_of(1) == USER


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (Catch("Frillish", shiny=False), PokemonEvent(2, USER, 10.0, "catch", "Frillish", False)),
        (Catch("Mr. Mime", shiny=True), PokemonEvent(2, USER, 10.0, "catch", "Mr. Mime", True)),
        (Cooldown(wait_seconds=6616), PokemonEvent(2, USER, 10.0, "cooldown")),
        (Cooldown(wait_seconds=None), PokemonEvent(2, USER, 10.0, "cooldown")),
    ],
    ids=["catch", "shiny", "cooldown", "cooldown-unknown-wait"],
)
def test_to_event(result: Catch | Cooldown, expected: PokemonEvent) -> None:
    assert to_event(result, 2, USER, 10.0) == expected


# --- cog -----------------------------------------------------------------------------


def make_message(
    message_id: int,
    author_id: int,
    content: str = "",
    *,
    bot: bool = False,
    embeds: list[dict[str, Any]] | None = None,
    reply_to: int | None = None,
    guild_id: int | None = GUILD_ID,
) -> Any:
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(id=author_id, bot=bot),
        content=content,
        embeds=[discord.Embed.from_dict(embed) for embed in embeds or []],  # type: ignore[arg-type]
        reference=None if reply_to is None else SimpleNamespace(message_id=reply_to),
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        created_at=REPLY_TIME,
    )


def make_tracker(tmp_path: Path, clock: FakeClock | None = None) -> PokemonTracker:
    store = SqliteActivityStore(tmp_path / "activity.sqlite3")
    return PokemonTracker(store, TOASTY, GUILD_ID, PendingCommands(clock or FakeClock()))


async def events(tracker: PokemonTracker) -> list[PokemonEvent]:
    return await tracker.store.pokemon_events_between(0.0, 1e12)


async def command_then_reply(tracker: PokemonTracker, reply: Any) -> None:
    await tracker.on_message(make_message(1, USER, ";pokemon"))
    await tracker.on_message(reply)


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_cog_records_fixture_replies(tmp_path: Path, case: dict[str, Any]) -> None:
    tracker = make_tracker(tmp_path)
    author = TOASTY if case["from_toasty"] else OTHER_BOT
    await tracker.on_message(make_message(1, USER, case["invoker_content"]))
    await tracker.on_message(
        make_message(
            2,
            author,
            case["reply_content"],
            bot=True,
            embeds=case["reply_embeds"],
            reply_to=1,
        )
    )

    expected = case["expected"]
    recorded = await events(tracker)
    if expected is None:
        assert recorded == []
    elif expected["kind"] == "catch":
        assert recorded == [
            PokemonEvent(
                2, USER, REPLY_TIME.timestamp(), "catch", expected["pokemon"], expected["shiny"]
            )
        ]
    else:
        assert recorded == [PokemonEvent(2, USER, REPLY_TIME.timestamp(), "cooldown")]


CATCH_TEXT = "<@999>, you've caught a Frillish!"


async def test_reply_to_unknown_message_is_ignored(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    await command_then_reply(tracker, make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=42))

    assert await events(tracker) == []


async def test_toasty_message_without_reference_is_ignored(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    await command_then_reply(tracker, make_message(2, TOASTY, CATCH_TEXT, bot=True))

    assert await events(tracker) == []


async def test_bot_authored_command_is_not_remembered(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    await tracker.on_message(make_message(1, OTHER_BOT, ";pokemon", bot=True))
    await tracker.on_message(make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1))

    assert await events(tracker) == []


async def test_non_command_message_is_not_remembered(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    await tracker.on_message(make_message(1, USER, ";pokemon list"))
    await tracker.on_message(make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1))

    assert await events(tracker) == []


async def test_reply_after_ttl_is_ignored(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = make_tracker(tmp_path, clock)
    await tracker.on_message(make_message(1, USER, ";pokemon"))
    clock.now = 301.0
    await tracker.on_message(make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1))

    assert await events(tracker) == []


async def test_duplicate_delivery_records_once(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    reply = make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1)
    await command_then_reply(tracker, reply)
    await tracker.on_message(reply)

    assert len(await events(tracker)) == 1


async def test_edited_reply_is_recorded_once(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    loading = make_message(2, TOASTY, "Throwing a Poké Ball...", bot=True, reply_to=1)
    await command_then_reply(tracker, loading)
    assert await events(tracker) == []

    edited = make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1)
    await tracker.on_message_edit(loading, edited)
    await tracker.on_message_edit(edited, edited)

    assert await events(tracker) == [
        PokemonEvent(2, USER, REPLY_TIME.timestamp(), "catch", "Frillish", False)
    ]


async def test_edit_by_other_author_is_ignored(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    await tracker.on_message(make_message(1, USER, ";pokemon"))
    edited = make_message(2, OTHER_BOT, CATCH_TEXT, bot=True, reply_to=1)
    await tracker.on_message_edit(edited, edited)

    assert await events(tracker) == []


async def test_other_guild_is_ignored(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    await tracker.on_message(make_message(1, USER, ";pokemon", guild_id=2))
    await tracker.on_message(make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1, guild_id=2))

    assert await events(tracker) == []
    assert len(tracker.pending) == 0


class FailingStore:
    async def record_pokemon_event(self, event: PokemonEvent) -> None:
        raise RuntimeError("disk full")


async def test_store_errors_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    tracker = PokemonTracker(FailingStore(), TOASTY, GUILD_ID, PendingCommands(FakeClock()))  # type: ignore[arg-type]
    await command_then_reply(tracker, make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1))

    assert "Failed to record a Toasty reply" in caplog.text


def setup_bot(toasty_bot_id: int | None, tmp_path: Path) -> Any:
    added: list[Any] = []

    async def add_cog(cog: Any) -> None:
        added.append(cog)

    config = SimpleNamespace(
        data_dir=str(tmp_path),
        guild_id=GUILD_ID,
        activity_timezone="UTC",
        activity_channel_id=None,
        toasty_bot_id=toasty_bot_id,
    )
    return SimpleNamespace(config=config, add_cog=add_cog, added=added)


@pytest.mark.parametrize(("toasty_bot_id", "registered"), [(None, False), (TOASTY, True)])
async def test_setup_adds_tracker_only_with_toasty_bot_id(
    tmp_path: Path, toasty_bot_id: int | None, registered: bool
) -> None:
    from ninja_bot.cogs.activity import setup

    bot = setup_bot(toasty_bot_id, tmp_path)
    await setup(bot)

    trackers = [cog for cog in bot.added if isinstance(cog, PokemonTracker)]
    assert bool(trackers) is registered
    if registered:
        assert trackers[0].store is bot.added[0].store


async def test_reply_is_stamped_with_its_creation_time(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    reply = make_message(2, TOASTY, CATCH_TEXT, bot=True, reply_to=1)
    reply.created_at = datetime(2026, 10, 1, 8, 30, tzinfo=UTC)
    reply.edited_at = REPLY_TIME
    await command_then_reply(tracker, reply)

    assert [event.ts for event in await events(tracker)] == [reply.created_at.timestamp()]
