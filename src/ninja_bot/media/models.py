from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Literal

MediaKind = Literal["mp3", "mp4"]


class MediaError(Exception):
    """Base for media errors; `message` is safe to show to Discord users."""

    default_message = "Something went wrong with that download."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class InvalidUrlError(MediaError):
    default_message = "That doesn't look like a valid http(s) link."


class UnsupportedMediaError(MediaError):
    default_message = "That media can't be downloaded."


class TooLargeError(MediaError):
    default_message = "That file is too large to upload to this server."


class DownloadError(MediaError):
    default_message = "The download failed. The link may be private, removed or unsupported."


@dataclass(frozen=True)
class VideoInfo:
    title: str
    uploader: str | None
    duration_s: int | None
    thumbnail: str | None
    webpage_url: str
    view_count: int | None
    upload_date: datetime.date | None
    is_live: bool
    is_playlist: bool


def parse_info(raw: dict[str, Any]) -> VideoInfo:
    """Map a yt-dlp info dict to a VideoInfo, tolerating missing or odd values."""
    return VideoInfo(
        title=_opt_str(raw.get("title")) or "Untitled",
        uploader=_opt_str(raw.get("uploader")),
        duration_s=_opt_int(raw.get("duration")),
        thumbnail=_opt_str(raw.get("thumbnail")),
        webpage_url=_opt_str(raw.get("webpage_url")) or _opt_str(raw.get("original_url")) or "",
        view_count=_opt_int(raw.get("view_count")),
        upload_date=_parse_date(raw.get("upload_date")),
        is_live=bool(raw.get("is_live")),
        is_playlist=raw.get("_type") == "playlist",
    )


def format_duration(seconds: int | None) -> str:
    """Format seconds as m:ss or h:mm:ss; None becomes 'Unknown'."""
    if seconds is None:
        return "Unknown"
    hours, rest = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _opt_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _parse_date(value: Any) -> datetime.date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None
