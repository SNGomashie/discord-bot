import asyncio
import dataclasses
import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from ninja_bot.cogs.media import (
    DEFAULT_UPLOAD_LIMIT_BYTES,
    GENERIC_ERROR_MESSAGE,
    Media,
    cooldown_message,
    upload_limit,
)
from ninja_bot.media.clip import Clip
from ninja_bot.media.models import (
    DownloadError,
    InvalidClipError,
    InvalidUrlError,
    MediaError,
    TooLargeError,
    UnsupportedMediaError,
    VideoInfo,
)
from ninja_bot.media.planning import DownloadPlan

URL = "https://example.com/watch?v=1"
INFO = VideoInfo(
    title="Test video",
    uploader="Someone",
    duration_s=60,
    thumbnail=None,
    webpage_url=URL,
    view_count=10,
    upload_date=datetime.date(2026, 1, 2),
    is_live=False,
    is_playlist=False,
)
GUILD_LIMIT = 25 * 1024 * 1024


class FakeDownloader:
    def __init__(
        self,
        info: VideoInfo = INFO,
        info_error: Exception | None = None,
        download_error: Exception | None = None,
    ) -> None:
        self.info = info
        self.info_error = info_error
        self.download_error = download_error
        self.fetched: list[str] = []
        self.plans: list[DownloadPlan] = []
        self.workdirs: list[Path] = []

    async def fetch_info(self, url: str) -> VideoInfo:
        self.fetched.append(url)
        if self.info_error is not None:
            raise self.info_error
        return self.info

    async def download(self, url: str, plan: DownloadPlan, workdir: Path) -> Path:
        self.plans.append(plan)
        self.workdirs.append(workdir)
        if self.download_error is not None:
            raise self.download_error
        return write_file(workdir / f"video.{plan.kind}")


def write_file(path: Path) -> Path:
    path.write_bytes(b"data")
    return path


def exists(path: Path) -> bool:
    return path.exists()


def make_interaction(guild_limit: int | None = GUILD_LIMIT) -> MagicMock:
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    if guild_limit is None:
        interaction.guild = None
    else:
        interaction.guild.filesize_limit = guild_limit
    return interaction


def followup_kwargs(interaction: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    interaction.followup.send.assert_awaited_once()
    call = interaction.followup.send.await_args
    return call.args, call.kwargs


def test_download_command_format_choices() -> None:
    param = Media.download._params["format"]
    assert [c.value for c in param.choices] == ["mp3", "mp4"]
    assert not param.required
    assert param.default == "mp4"


@pytest.mark.parametrize("name", ["start", "end"])
def test_download_command_clip_params_are_optional_strings(name: str) -> None:
    param = Media.download._params[name]
    assert not param.required
    assert param.default is None
    assert param.type is discord.AppCommandOptionType.string
    assert ":" in str(param.description)  # example formats


def test_upload_limit() -> None:
    guild = MagicMock()
    guild.filesize_limit = GUILD_LIMIT
    assert upload_limit(guild) == GUILD_LIMIT
    assert upload_limit(None) == DEFAULT_UPLOAD_LIMIT_BYTES


async def test_success_mp4_default() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()
    cog = Media(downloader, max_duration_s=3600)

    await Media.download.callback(cog, interaction, URL)

    interaction.response.defer.assert_awaited_once_with(thinking=True)
    assert downloader.fetched == [URL]
    assert downloader.plans[0].kind == "mp4"
    assert downloader.plans[0].limit_bytes == GUILD_LIMIT
    _, kwargs = followup_kwargs(interaction)
    assert isinstance(kwargs["file"], discord.File)
    assert kwargs["file"].filename == "video.mp4"
    assert kwargs["embed"].footer.text == "MP4"
    assert kwargs["embed"].title == "Test video"
    assert not exists(downloader.workdirs[0])


async def test_success_mp3() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()
    cog = Media(downloader, max_duration_s=3600)

    await cog.handle_download(interaction, f"  {URL}  ", "mp3")

    assert downloader.fetched == [URL]
    assert downloader.plans[0].kind == "mp3"
    _, kwargs = followup_kwargs(interaction)
    assert kwargs["file"].filename == "video.mp3"
    assert kwargs["embed"].footer.text == "MP3"
    assert not exists(downloader.workdirs[0])


async def test_no_guild_uses_default_limit() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction(guild_limit=None)

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    assert downloader.plans[0].limit_bytes == DEFAULT_UPLOAD_LIMIT_BYTES


async def test_invalid_url_replies_ephemeral_before_defer() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, "ftp://nope", "mp4")

    interaction.response.send_message.assert_awaited_once_with(
        InvalidUrlError.default_message, ephemeral=True
    )
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    assert downloader.fetched == []


async def test_fetch_info_download_error_has_no_embed() -> None:
    downloader = FakeDownloader(info_error=DownloadError("Couldn't read that link."))
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    args, kwargs = followup_kwargs(interaction)
    assert args == ("Couldn't read that link.",)
    assert "embed" not in kwargs
    assert downloader.plans == []


async def test_download_error_after_info_has_no_embed() -> None:
    downloader = FakeDownloader(download_error=DownloadError())
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    args, kwargs = followup_kwargs(interaction)
    assert args == (DownloadError.default_message,)
    assert "embed" not in kwargs
    assert not exists(downloader.workdirs[0])


async def test_unsupported_media_includes_embed() -> None:
    downloader = FakeDownloader(info=dataclasses.replace(INFO, is_live=True))
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    args, kwargs = followup_kwargs(interaction)
    assert args == ("Live streams can't be downloaded.",)
    assert kwargs["embed"].title == "Test video"
    assert downloader.plans == []


async def test_too_long_is_unsupported_with_embed() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media(downloader, max_duration_s=30).handle_download(interaction, URL, "mp3")

    args, kwargs = followup_kwargs(interaction)
    assert "too long" in args[0]
    assert kwargs["embed"].footer.text == "MP3"


async def test_too_large_from_download_includes_embed() -> None:
    downloader = FakeDownloader(download_error=TooLargeError())
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    args, kwargs = followup_kwargs(interaction)
    assert args == (TooLargeError.default_message,)
    assert kwargs["embed"].title == "Test video"
    assert not exists(downloader.workdirs[0])


@pytest.mark.parametrize(
    "error",
    [InvalidUrlError(), UnsupportedMediaError(), TooLargeError(), DownloadError(), MediaError()],
)
async def test_each_media_error_from_fetch_info(error: MediaError) -> None:
    downloader = FakeDownloader(info_error=error)
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    args, kwargs = followup_kwargs(interaction)
    assert args == (error.message,)
    assert "embed" not in kwargs  # no info yet


async def test_unexpected_error_is_generic_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    downloader = FakeDownloader(download_error=RuntimeError("secret /internal/path"))
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4")

    args, kwargs = followup_kwargs(interaction)
    assert args == (GENERIC_ERROR_MESSAGE,)
    assert kwargs == {}
    assert "secret" not in GENERIC_ERROR_MESSAGE
    assert any(r.exc_info for r in caplog.records)
    assert not exists(downloader.workdirs[0])


class BlockingDownloader(FakeDownloader):
    """Fake whose first download blocks until `release` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def download(self, url: str, plan: DownloadPlan, workdir: Path) -> Path:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return await super().download(url, plan, workdir)
        finally:
            self.active -= 1


async def test_second_request_waits_for_first() -> None:
    downloader = BlockingDownloader()
    cog = Media(downloader, 3600)
    first_interaction = make_interaction()
    second_interaction = make_interaction()

    first = asyncio.create_task(cog.handle_download(first_interaction, URL, "mp4"))
    await downloader.started.wait()
    second = asyncio.create_task(cog.handle_download(second_interaction, URL, "mp3"))
    for _ in range(5):
        await asyncio.sleep(0)

    # The second request is deferred but has not started fetching yet.
    second_interaction.response.defer.assert_awaited_once_with(thinking=True)
    assert downloader.fetched == [URL]
    assert not second.done()

    downloader.release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

    assert downloader.fetched == [URL, URL]
    assert [p.kind for p in downloader.plans] == ["mp4", "mp3"]
    assert downloader.max_active == 1
    first_interaction.followup.send.assert_awaited_once()
    second_interaction.followup.send.assert_awaited_once()


async def test_semaphore_released_after_error() -> None:
    cog = Media(FakeDownloader(download_error=RuntimeError("boom")), 3600)

    await cog.handle_download(make_interaction(), URL, "mp4")

    assert not cog.semaphore.locked()


async def test_semaphore_not_acquired_for_invalid_url() -> None:
    cog = Media(FakeDownloader(), 3600)
    await cog.semaphore.acquire()

    await asyncio.wait_for(cog.handle_download(make_interaction(), "nope", "mp4"), timeout=1)


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [(29.2, "30 seconds"), (1.0, "1 second"), (0.1, "1 second"), (2.0, "2 seconds")],
)
def test_cooldown_message(retry_after: float, expected: str) -> None:
    assert cooldown_message(retry_after).endswith(f"in {expected}.")


async def test_download_has_per_user_cooldown() -> None:
    check = Media.download.checks[0]
    now = discord.utils.utcnow()
    interaction = make_interaction()
    interaction.user.id = 1
    interaction.created_at = now
    other = make_interaction()
    other.user.id = 2
    other.created_at = now

    assert await check(interaction)
    with pytest.raises(app_commands.CommandOnCooldown) as exc_info:
        await check(interaction)
    assert exc_info.value.cooldown.per == 30
    assert await check(other)


async def test_cooldown_error_replies_ephemeral_with_remaining_time() -> None:
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=False)
    error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 30), retry_after=12.3)

    await Media(FakeDownloader(), 3600).cog_app_command_error(interaction, error)

    interaction.response.send_message.assert_awaited_once_with(
        cooldown_message(12.3), ephemeral=True
    )
    assert "13 seconds" in cooldown_message(12.3)


async def test_other_command_error_is_generic_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=True)
    error = app_commands.CheckFailure("secret detail")

    await Media(FakeDownloader(), 3600).cog_app_command_error(interaction, error)

    interaction.followup.send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE, ephemeral=True)
    interaction.response.send_message.assert_not_awaited()
    assert any(r.exc_info for r in caplog.records)


def embed_field(embed: discord.Embed, name: str) -> str | None:
    return next((f.value for f in embed.fields if f.name == name), None)


async def test_no_clip_has_no_clip_field() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media.download.callback(Media(downloader, 3600), interaction, URL)

    assert downloader.plans[0].clip is None
    _, kwargs = followup_kwargs(interaction)
    assert embed_field(kwargs["embed"], "Clip") is None


async def test_start_only_clips_to_end_of_video() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media.download.callback(Media(downloader, 3600), interaction, URL, "mp3", start="0:15")

    assert downloader.plans[0].kind == "mp3"
    assert downloader.plans[0].clip == Clip(15, 60)
    _, kwargs = followup_kwargs(interaction)
    assert embed_field(kwargs["embed"], "Clip") == "0:15 → 1:00 (0:45)"
    assert embed_field(kwargs["embed"], "Duration") == "1:00"


async def test_start_and_end_clip_reaches_downloader() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4", "10", "20.5")

    interaction.response.defer.assert_awaited_once_with(thinking=True)
    assert downloader.plans[0].clip == Clip(10, 20.5)
    _, kwargs = followup_kwargs(interaction)
    assert kwargs["file"].filename == "video.mp4"
    assert embed_field(kwargs["embed"], "Clip") == "0:10 → 0:20.5 (0:10.5)"


async def test_clip_under_cap_on_longer_video_is_allowed() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media(downloader, max_duration_s=30).handle_download(
        interaction, URL, "mp3", "0:10", "0:30"
    )

    assert downloader.plans[0].clip == Clip(10, 30)


@pytest.mark.parametrize(
    ("start", "end"),
    [(None, "1:00"), ("", "1:00"), ("0:30", "0:10"), ("0:10", "0:10"), ("abc", None)],
)
async def test_invalid_clip_replies_ephemeral_before_defer(
    start: str | None, end: str | None
) -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()
    cog = Media(downloader, 3600)
    await cog.semaphore.acquire()  # an invalid clip must not wait for the semaphore

    await asyncio.wait_for(cog.handle_download(interaction, URL, "mp4", start, end), timeout=1)

    interaction.response.send_message.assert_awaited_once()
    message = interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs == {"ephemeral": True}
    assert isinstance(message, str) and message
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    assert downloader.fetched == []


async def test_end_without_start_message() -> None:
    interaction = make_interaction()

    await Media(FakeDownloader(), 3600).handle_download(interaction, URL, "mp4", None, "1:00")

    message = interaction.response.send_message.await_args.args[0]
    assert "start time too" in message


async def test_start_beyond_video_is_followup_error() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media(downloader, 3600).handle_download(interaction, URL, "mp4", "2:00")

    interaction.response.defer.assert_awaited_once_with(thinking=True)
    args, kwargs = followup_kwargs(interaction)
    assert "past the end of the video" in args[0]
    assert "1:00" in args[0]
    assert "embed" not in kwargs
    assert downloader.plans == []


async def test_clip_too_long_error_embed_has_no_clip() -> None:
    downloader = FakeDownloader()
    interaction = make_interaction()

    await Media(downloader, max_duration_s=10).handle_download(interaction, URL, "mp3", "0:05")

    args, kwargs = followup_kwargs(interaction)
    assert "clip" in args[0].lower()
    assert embed_field(kwargs["embed"], "Clip") is None
    assert downloader.plans == []


def test_invalid_clip_error_is_media_error() -> None:
    assert issubclass(InvalidClipError, MediaError)
