from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

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


class Media(commands.Cog):
    def __init__(self, downloader: Downloader, max_duration_s: int) -> None:
        self.downloader = downloader
        self.max_duration_s = max_duration_s

    @app_commands.command(name="download", description="Download a video as mp3 or mp4")
    @app_commands.describe(url="Link to the video", format="File format (default mp4)")
    async def download(
        self, interaction: discord.Interaction, url: str, format: MediaKind = "mp4"
    ) -> None:
        await self.handle_download(interaction, url, format)

    async def handle_download(
        self, interaction: discord.Interaction, url: str, kind: MediaKind
    ) -> None:
        try:
            clean_url = validate_url(url)
        except MediaError as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        info: VideoInfo | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="ninja-dl-") as tmp:
                info = await self.downloader.fetch_info(clean_url)
                plan = plan_download(
                    info, kind, upload_limit(interaction.guild), self.max_duration_s
                )
                path = await self.downloader.download(clean_url, plan, Path(tmp))
                await interaction.followup.send(
                    embed=build_embed(info, kind), file=discord.File(path)
                )
        except MediaError as exc:
            embed = error_embed(exc, info, kind)
            if embed is None:
                await interaction.followup.send(exc.message)
            else:
                await interaction.followup.send(exc.message, embed=embed)
        except Exception:
            log.exception("Unexpected error downloading %s", clean_url)
            await interaction.followup.send(GENERIC_ERROR_MESSAGE)


async def setup(bot: NinjaBot) -> None:
    await bot.add_cog(Media(YtDlpDownloader(), bot.config.max_duration_seconds))
