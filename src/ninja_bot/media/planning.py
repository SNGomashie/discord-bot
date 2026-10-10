from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlsplit

from ninja_bot.media.clip import Clip, resolve_clip
from ninja_bot.media.models import (
    InvalidUrlError,
    MediaKind,
    TooLargeError,
    UnsupportedMediaError,
    VideoInfo,
    format_duration,
)

AUDIO_BITRATES_KBPS: tuple[int, ...] = (192, 128, 96, 64)
HEIGHT_LADDER: tuple[int, ...] = (1080, 720, 480, 360, 240)

# Conservative (low) estimate of total video+audio bitrate for a 240p mp4.
# Real 240p files are usually larger; we only reject up front when even this
# optimistic estimate cannot fit, and leave the rest to the real size check.
MIN_240P_BITRATE_KBPS = 200


@dataclass(frozen=True)
class DownloadPlan:
    kind: MediaKind
    limit_bytes: int
    audio_bitrate_kbps: int | None = None
    height_ladder: tuple[int, ...] = ()
    clip: Clip | None = None  # resolved: end_s is always set


def validate_url(text: str) -> str:
    """Return the stripped URL if it is http(s) with a host, else raise InvalidUrlError."""
    url = text.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise InvalidUrlError() from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise InvalidUrlError()
    return url


def estimated_size_bytes(duration_s: int, kbps: int) -> int:
    """Size in bytes of `duration_s` seconds at a constant `kbps` bitrate."""
    return duration_s * kbps * 1000 // 8


def plan_download(
    info: VideoInfo,
    kind: MediaKind,
    limit_bytes: int,
    max_duration_s: int,
    clip: Clip | None = None,
) -> DownloadPlan:
    """Choose download settings that should fit `limit_bytes`, or raise a MediaError.

    With a clip, the duration cap and size estimates apply to the clip length.
    """
    if info.is_live:
        raise UnsupportedMediaError("Live streams can't be downloaded.")
    if info.is_playlist:
        raise UnsupportedMediaError("Playlists aren't supported; send a link to a single video.")
    duration = info.duration_s
    if duration is None:
        raise UnsupportedMediaError(
            "I couldn't tell how long that video is, so I won't download it."
        )
    if clip is None:
        if duration > max_duration_s:
            raise UnsupportedMediaError(
                f"That video is too long (max {format_duration(max_duration_s)})."
            )
    else:
        clip = resolve_clip(clip, duration)
        assert clip.end_s is not None
        clip_length = clip.end_s - clip.start_s
        if clip_length > max_duration_s:
            raise UnsupportedMediaError(
                f"That clip is too long (max {format_duration(max_duration_s)})."
            )
        duration = math.ceil(clip_length)

    if kind == "mp3":
        for kbps in AUDIO_BITRATES_KBPS:
            if estimated_size_bytes(duration, kbps) <= limit_bytes:
                return DownloadPlan(
                    kind=kind, limit_bytes=limit_bytes, audio_bitrate_kbps=kbps, clip=clip
                )
        raise TooLargeError("That audio is too long to fit this server's upload limit.")

    if estimated_size_bytes(duration, MIN_240P_BITRATE_KBPS) > limit_bytes:
        raise TooLargeError("That video is too long to fit this server's upload limit.")
    return DownloadPlan(kind=kind, limit_bytes=limit_bytes, height_ladder=HEIGHT_LADDER, clip=clip)


def format_selector(plan: DownloadPlan, height: int) -> str:
    """yt-dlp format string: height <= `height`, size within the limit, prefer mp4/m4a."""
    if plan.kind != "mp4":
        raise ValueError("format_selector is only for mp4 plans")
    size = f"[filesize<=?{plan.limit_bytes}][filesize_approx<=?{plan.limit_bytes}]"
    video = f"bv*[height<={height}]"
    best = f"b[height<={height}]"
    return "/".join(
        [
            f"{video}[ext=mp4]{size}+ba[ext=m4a]{size}",
            f"{video}{size}+ba{size}",
            f"{best}[ext=mp4]{size}",
            f"{best}{size}",
        ]
    )
