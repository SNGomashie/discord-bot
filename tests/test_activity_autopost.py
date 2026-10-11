"""Monthly activity auto-post: the hourly cycle, channel checks and confirm rules."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ninja_bot.activity.embed import SHAME_FIELD, build_monthly_embed
from ninja_bot.activity.store import SqliteActivityStore
from ninja_bot.cogs.activity import REPORT_CHECK_SECONDS, Activity
from ninja_bot.cs2.channels import (
    CHANNEL_MISSING,
    CHANNEL_NO_PERMISSIONS,
    CHANNEL_NOT_TEXT,
    CHANNEL_UNAVAILABLE,
)

CHANNEL_ID = 555
USER = 42
ALL_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def ts(month: int, day: int, hour: int = 12) -> float:
    return datetime(2026, month, day, hour, tzinfo=UTC).timestamp()


def make_channel(permissions: discord.Permissions = ALL_PERMISSIONS) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    channel.guild.id = 1
    channel.guild.get_member.return_value = None
    channel.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="Member"))
    channel.permissions_for.return_value = permissions
    channel.send = AsyncMock()
    return channel


def make_bot(channel: object | None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
    )
    bot.wait_until_ready = AsyncMock()
    return bot


async def make_store(tmp_path: Path, *months: int) -> SqliteActivityStore:
    store = SqliteActivityStore(tmp_path / "activity.sqlite3")
    for month in months:
        await store.open_session(USER, 10, ts(month, 10))
        await store.close_session(USER, ts(month, 10) + 7200)
    return store


def make_cog(
    store: SqliteActivityStore,
    bot: MagicMock,
    clock: FakeClock,
    channel_id: int | None = CHANNEL_ID,
) -> Activity:
    return Activity(bot, store, 1, clock=clock, channel_id=channel_id)


def sent_titles(channel: MagicMock) -> list[str]:
    return [str(call.kwargs["embed"].title) for call in channel.send.await_args_list]


async def test_posts_ended_month_once(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 10)
    channel = make_channel()
    clock = FakeClock(ts(10, 31, 23))
    cog = make_cog(store, make_bot(channel), clock)
    await cog.run_report_cycle()
    channel.send.assert_not_awaited()
    clock.now = ts(11, 1, 0)
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    assert channel.send.await_count == 1
    kwargs: Any = channel.send.await_args.kwargs
    assert "October 2026" in str(kwargs["embed"].title)
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert await store.report_posted("2026-10") is True


async def test_missed_months_post_oldest_first_one_per_cycle(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 8, 10)
    channel = make_channel()
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    assert len(sent_titles(channel)) == 1
    assert "August 2026" in sent_titles(channel)[0]
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    titles = sent_titles(channel)
    assert len(titles) == 2
    assert "October 2026" in titles[1]
    assert await store.report_posted("2026-09") is False


async def test_failing_send_is_confirmed_and_not_repeated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await make_store(tmp_path, 10)
    channel = make_channel()
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    assert channel.send.await_count == 1
    assert await store.report_posted("2026-10") is True
    assert "Could not send activity response" in caplog.text


async def test_build_error_is_retried_next_cycle(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_monthly_embed
    calls = 0

    def flaky(*args: Any, **kwargs: Any) -> discord.Embed:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bug")
        return real(*args, **kwargs)

    monkeypatch.setattr("ninja_bot.cogs.activity.build_monthly_embed", flaky)
    store = await make_store(tmp_path, 10)
    channel = make_channel()
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    channel.send.assert_not_awaited()
    assert await store.report_posted("2026-10") is False
    assert "Could not build the activity overview for 2026-10" in caplog.text
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    assert channel.send.await_count == 1
    assert await store.report_posted("2026-10") is True


async def test_build_error_every_cycle_is_logged_and_never_confirmed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await make_store(tmp_path, 10)
    channel = make_channel()
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 2)))
    cog.monthly_report = AsyncMock(side_effect=RuntimeError("locked"))  # type: ignore[method-assign]
    for _ in range(3):
        await cog.run_report_cycle()
    channel.send.assert_not_awaited()
    assert await store.report_posted("2026-10") is False
    failures = [r for r in caplog.records if "Could not build the activity overview" in r.message]
    assert len(failures) == 3
    assert all(r.exc_info is not None for r in failures)


async def test_unexpected_send_error_is_confirmed(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 10)
    channel = make_channel()
    channel.send = AsyncMock(side_effect=RuntimeError("bug"))
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    assert channel.send.await_count == 1
    assert await store.report_posted("2026-10") is True


async def test_store_error_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await make_store(tmp_path, 10)
    store.first_session_start = AsyncMock(side_effect=RuntimeError("locked"))  # type: ignore[method-assign]
    cog = make_cog(store, make_bot(make_channel()), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    assert "Unexpected error in a monthly activity post cycle" in caplog.text


async def test_nothing_due_does_not_touch_the_channel(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 10)
    bot = make_bot(None)
    cog = make_cog(store, bot, FakeClock(ts(10, 20)))
    await cog.run_report_cycle()
    bot.get_channel.assert_not_called()


async def test_channel_is_fetched_when_not_cached(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 10)
    channel = make_channel()
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(return_value=channel)
    cog = make_cog(store, bot, FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    bot.fetch_channel.assert_awaited_once_with(CHANNEL_ID)
    assert channel.send.await_count == 1


@pytest.mark.parametrize(
    "error",
    [
        discord.NotFound(MagicMock(status=404), "Unknown Channel"),
        discord.Forbidden(MagicMock(status=403), "Missing Access"),
    ],
)
async def test_missing_channel_warns_once_and_stays_unposted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, error: discord.HTTPException
) -> None:
    store = await make_store(tmp_path, 10)
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(side_effect=error)
    cog = make_cog(store, bot, FakeClock(ts(11, 2)))
    with caplog.at_level(logging.WARNING):
        await cog.run_report_cycle()
        await cog.run_report_cycle()
    assert caplog.text.count(CHANNEL_MISSING) == 1
    assert "ACTIVITY_CHANNEL_ID" in caplog.text
    assert "CS2" not in caplog.text
    # Nothing was sent, so the month posts once the channel is fixed.
    assert await store.report_posted("2026-10") is False


async def test_fetch_http_error_warns_once_and_stays_unposted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await make_store(tmp_path, 10)
    bot = make_bot(None)
    bot.fetch_channel = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=503), "Service Unavailable")
    )
    cog = make_cog(store, bot, FakeClock(ts(11, 2)))
    with caplog.at_level(logging.WARNING):
        await cog.run_report_cycle()
        await cog.run_report_cycle()
    assert (
        caplog.text.count(
            "Not posting the monthly activity overview: the ACTIVITY_CHANNEL_ID channel is "
            f"{CHANNEL_UNAVAILABLE}"
        )
        == 1
    )
    assert "Unexpected error" not in caplog.text
    assert await store.report_posted("2026-10") is False


async def test_non_text_channel_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await make_store(tmp_path, 10)
    cog = make_cog(store, make_bot(MagicMock(spec=discord.CategoryChannel)), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    assert caplog.text.count(CHANNEL_NOT_TEXT) == 1


async def test_missing_permissions_warn_once_then_post_when_fixed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await make_store(tmp_path, 10)
    channel = make_channel(discord.Permissions(view_channel=True, send_messages=True))
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 2)))
    await cog.run_report_cycle()
    await cog.run_report_cycle()
    assert caplog.text.count(CHANNEL_NO_PERMISSIONS) == 1
    channel.send.assert_not_awaited()
    channel.permissions_for.return_value = ALL_PERMISSIONS
    await cog.run_report_cycle()
    assert channel.send.await_count == 1


async def test_cycle_does_nothing_without_channel(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 10)
    bot = make_bot(make_channel())
    cog = make_cog(store, bot, FakeClock(ts(11, 2)), channel_id=None)
    await cog.run_report_cycle()
    bot.get_channel.assert_not_called()


async def test_loop_starts_only_with_channel_and_stops_on_unload(tmp_path: Path) -> None:
    bot = make_bot(None)
    bot.wait_until_ready = AsyncMock(side_effect=asyncio.Event().wait)
    cog = make_cog(await make_store(tmp_path), bot, FakeClock(ts(11, 2)))
    await cog.cog_load()
    assert cog.report_loop.is_running()
    assert cog.report_loop.seconds == REPORT_CHECK_SECONDS
    await asyncio.sleep(0)
    bot.wait_until_ready.assert_awaited_once()
    await cog.cog_unload()
    await asyncio.sleep(0)
    assert not cog.report_loop.is_running()


async def test_loop_not_started_without_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cog = make_cog(await make_store(tmp_path), make_bot(None), FakeClock(ts(11, 2)), None)
    with caplog.at_level(logging.DEBUG, logger="ninja_bot.cogs.activity"):
        await cog.cog_load()
    assert not cog.report_loop.is_running()
    assert "monthly activity posts are off" in caplog.text


async def test_monthly_post_skips_departed_members(tmp_path: Path) -> None:
    store = await make_store(tmp_path, 10)
    other = USER + 1
    await store.open_session(other, 10, ts(10, 2))
    await store.close_session(other, ts(10, 2) + 60)
    channel = make_channel()
    channel.guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )
    cog = make_cog(store, make_bot(channel), FakeClock(ts(11, 1, 1)))
    await cog.run_report_cycle()

    embed = channel.send.await_args.kwargs["embed"]
    assert SHAME_FIELD not in [f.name for f in embed.fields]
    assert channel.guild.fetch_member.await_count == 2
