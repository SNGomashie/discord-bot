from __future__ import annotations

import asyncio
import logging
import math
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ninja_bot.media.clip import MAX_TIMESTAMP_LENGTH, Clip, parse_clip
from ninja_bot.media.cooldown import UserCooldown
from ninja_bot.media.downloader import Downloader, YtDlpDownloader
from ninja_bot.media.embed import build_embed
from ninja_bot.media.models import (
    MediaError,
    MediaKind,
    TooLargeError,
    UnsupportedMediaError,
    VideoInfo,
)
from ninja_bot.media.planning import plan_download, validate_url

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

# Discord's base upload limit, used when there is no guild (e.g. DMs).
DEFAULT_UPLOAD_LIMIT_BYTES = 10 * 1024 * 1024
GENERIC_ERROR_MESSAGE = "Something went wrong while downloading that. Please try again later."
COOLDOWN_SECONDS = 30.0


def upload_limit(guild: discord.Guild | None) -> int:
    """Max attachment size in bytes for `guild`, or the default without one."""
    if guild is None:
        return DEFAULT_UPLOAD_LIMIT_BYTES
    return guild.filesize_limit


def error_embed(exc: MediaError, info: VideoInfo | None, kind: MediaKind) -> discord.Embed | None:
    """Embed to show with an error reply: only for size/support errors once info is known."""
    if info is not None and isinstance(exc, TooLargeError | UnsupportedMediaError):
        return build_embed(info, kind)
    return None


def cooldown_message(retry_after: float) -> str:
    """User-facing reply for a /download cooldown, rounded up to whole seconds."""
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return f"Slow down! You can use /download again in {seconds} {unit}."


class Media(commands.Cog):
    def __init__(
        self,
        downloader: Downloader,
        max_duration_s: int,
        cooldown: UserCooldown | None = None,
    ) -> None:
        self.downloader = downloader
        self.max_duration_s = max_duration_s
        # Taken only after the input is valid and refunded unless a file is posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(COOLDOWN_SECONDS)
        # One download at a time keeps the homelab LXC from being overloaded.
        self.semaphore = asyncio.Semaphore(1)

    @app_commands.command(name="download", description="Download a video as mp3 or mp4")
    @app_commands.describe(
        url="Link to the video",
        format="File format (default mp4)",
        start="Clip start, e.g. 1:30, 90 or 1m30s (optional)",
        end="Clip end, e.g. 2:45, 165 or 2m45s (optional, needs start)",
    )
    async def download(
        self,
        interaction: discord.Interaction,
        url: str,
        format: MediaKind = "mp4",
        start: app_commands.Range[str, 0, MAX_TIMESTAMP_LENGTH] | None = None,
        end: app_commands.Range[str, 0, MAX_TIMESTAMP_LENGTH] | None = None,
    ) -> None:
        await self.handle_download(interaction, url, format, start, end)

    async def handle_download(
        self,
        interaction: discord.Interaction,
        url: str,
        kind: MediaKind,
        start: str | None = None,
        end: str | None = None,
    ) -> None:
        try:
            clean_url = validate_url(url)
            clip = parse_clip(start, end)
        except MediaError as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return

        user_id = interaction.user.id
        retry_after = self.cooldown.try_acquire(user_id)
        if retry_after > 0:
            await interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            return

        posted = False
        try:
            await interaction.response.defer(thinking=True)
            async with self.semaphore:
                posted = await self._download_deferred(interaction, clean_url, kind, clip)
        finally:
            if not posted:
                self.cooldown.refund(user_id)

    async def _download_deferred(
        self,
        interaction: discord.Interaction,
        clean_url: str,
        kind: MediaKind,
        clip: Clip | None,
    ) -> bool:
        """Fetch, download and post the file; return whether the file was posted."""
        info: VideoInfo | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="ninja-dl-") as tmp:
                info = await self.downloader.fetch_info(clean_url)
                plan = plan_download(
                    info, kind, upload_limit(interaction.guild), self.max_duration_s, clip=clip
                )
                path = await self.downloader.download(clean_url, plan, Path(tmp))
                await interaction.followup.send(
                    embed=build_embed(info, kind, plan.clip), file=discord.File(path)
                )
                return True
        except MediaError as exc:
            embed = error_embed(exc, info, kind)
            if embed is None:
                await interaction.followup.send(exc.message)
            else:
                await interaction.followup.send(exc.message, embed=embed)
        except Exception:
            log.exception("Unexpected error downloading %s", clean_url)
            await interaction.followup.send(GENERIC_ERROR_MESSAGE)
        return False

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Media cog", exc_info=error)
        message = GENERIC_ERROR_MESSAGE
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: NinjaBot) -> None:
    await bot.add_cog(Media(YtDlpDownloader(), bot.config.max_duration_seconds))
