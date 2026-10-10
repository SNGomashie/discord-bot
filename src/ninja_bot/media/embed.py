from __future__ import annotations

import discord

from ninja_bot.media.models import MediaKind, VideoInfo, format_duration

# Discord embed limits.
TITLE_LIMIT = 256
FIELD_VALUE_LIMIT = 1024


def build_embed(info: VideoInfo, kind: MediaKind) -> discord.Embed:
    """Build the info embed posted alongside a downloaded file."""
    embed = discord.Embed(
        title=_truncate(info.title, TITLE_LIMIT),
        url=info.webpage_url or None,
    )
    if info.uploader:
        _add_field(embed, "Uploader", info.uploader)
    if info.duration_s is not None:
        _add_field(embed, "Duration", format_duration(info.duration_s))
    if info.view_count is not None:
        _add_field(embed, "Views", f"{info.view_count:,}")
    if info.upload_date is not None:
        _add_field(embed, "Uploaded", info.upload_date.isoformat())
    if info.thumbnail:
        embed.set_thumbnail(url=info.thumbnail)
    embed.set_footer(text=kind.upper())
    return embed


def _add_field(embed: discord.Embed, name: str, value: str) -> None:
    embed.add_field(name=name, value=_truncate(value, FIELD_VALUE_LIMIT), inline=True)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
