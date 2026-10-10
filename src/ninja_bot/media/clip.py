from __future__ import annotations

import re
from dataclasses import dataclass

from ninja_bot.media.models import InvalidClipError, format_duration

# Longest accepted timestamp; also the max length of the /download start/end options.
MAX_TIMESTAMP_LENGTH = 32

# re.ASCII: `\d` must not match other Unicode digits.
_SECONDS_RE = re.compile(r"\d+(?:\.\d+)?", re.ASCII)
_COLON_RE = re.compile(r"(?:(\d+):)?(\d+):(\d{1,2}(?:\.\d+)?)", re.ASCII)
_UNITS_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", re.ASCII)


@dataclass(frozen=True)
class Clip:
    start_s: float
    end_s: float | None  # None = to the end of the video


def parse_timestamp(text: str) -> float:
    """Parse `90`, `90.5`, `1:30`, `1:02:03.5`, `1h2m3s`, `1m30.5s` into seconds."""
    value = text.strip().lower()
    # The length cap runs before any int()/float() so huge inputs can't overflow or stall.
    if not value or len(value) > MAX_TIMESTAMP_LENGTH:
        raise InvalidClipError()
    if _SECONDS_RE.fullmatch(value):
        return float(value)
    if match := _COLON_RE.fullmatch(value):
        hours_text, minutes_text, seconds_text = match.groups()
        minutes, seconds = int(minutes_text), float(seconds_text)
        if seconds >= 60 or (hours_text is not None and minutes >= 60):
            raise InvalidClipError()
        return int(hours_text or 0) * 3600 + minutes * 60 + seconds
    match = _UNITS_RE.fullmatch(value)
    if match and any(match.groups()):
        hours_text, minutes_text, seconds_text = match.groups()
        return int(hours_text or 0) * 3600 + int(minutes_text or 0) * 60 + float(seconds_text or 0)
    raise InvalidClipError()


def parse_clip(start: str | None, end: str | None) -> Clip | None:
    """Turn the optional start/end options into a Clip; None when neither is given."""
    start_text = (start or "").strip()
    end_text = (end or "").strip()
    if not start_text and not end_text:
        return None
    if not start_text:
        raise InvalidClipError("Give a start time too when you set an end time.")
    start_s = parse_timestamp(start_text)
    if not end_text:
        return Clip(start_s, None)
    end_s = parse_timestamp(end_text)
    if end_s <= start_s:
        raise InvalidClipError("The end time must be after the start time.")
    return Clip(start_s, end_s)


def resolve_clip(clip: Clip, duration_s: int) -> Clip:
    """Fit `clip` to a video of `duration_s` seconds, giving it a concrete end."""
    if clip.start_s >= duration_s:
        raise InvalidClipError(
            f"The start time is past the end of the video ({format_duration(duration_s)} long)."
        )
    end_s = duration_s if clip.end_s is None else min(clip.end_s, duration_s)
    if end_s - clip.start_s < 1:
        raise InvalidClipError("The clip must be at least 1 second long.")
    return Clip(clip.start_s, end_s)


def format_timestamp(seconds: float) -> str:
    """Format seconds as m:ss or h:mm:ss, adding one decimal only for fractional values."""
    whole, tenths = divmod(round(max(seconds, 0.0) * 10), 10)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    return f"{text}.{tenths}" if tenths else text
