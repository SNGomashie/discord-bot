import datetime
from dataclasses import replace

import discord
import pytest

from ninja_bot.media.clip import Clip
from ninja_bot.media.embed import FIELD_VALUE_LIMIT, TITLE_LIMIT, build_embed, format_clip
from ninja_bot.media.models import MediaKind, VideoInfo

FULL = VideoInfo(
    title="A video",
    uploader="Someone",
    duration_s=3725,
    thumbnail="https://img.example/t.jpg",
    webpage_url="https://example.com/watch?v=1",
    view_count=1234567,
    upload_date=datetime.date(2024, 1, 31),
    is_live=False,
    is_playlist=False,
)
MINIMAL = replace(
    FULL, uploader=None, duration_s=None, thumbnail=None, view_count=None, upload_date=None
)


def test_full_info() -> None:
    embed = build_embed(FULL, "mp4")
    assert embed.title == "A video"
    assert embed.url == "https://example.com/watch?v=1"
    assert [(f.name, f.value) for f in embed.fields] == [
        ("Uploader", "Someone"),
        ("Duration", "1:02:05"),
        ("Views", "1,234,567"),
        ("Uploaded", "2024-01-31"),
    ]
    assert embed.thumbnail.url == "https://img.example/t.jpg"
    assert embed.footer.text == "MP4"


def test_minimal_info_omits_missing_fields() -> None:
    embed = build_embed(MINIMAL, "mp3")
    assert embed.title == "A video"
    assert embed.url == "https://example.com/watch?v=1"
    assert embed.fields == []
    assert embed.thumbnail.url is None
    assert embed.footer.text == "MP3"


def test_empty_url_is_not_linked() -> None:
    embed = build_embed(replace(FULL, webpage_url=""), "mp4")
    assert embed.url is None


def test_very_long_title_is_truncated() -> None:
    embed = build_embed(replace(FULL, title="x" * 1000), "mp4")
    assert embed.title is not None
    assert len(embed.title) == TITLE_LIMIT
    assert embed.title.endswith("…")


def test_title_at_limit_is_unchanged() -> None:
    title = "y" * TITLE_LIMIT
    assert build_embed(replace(FULL, title=title), "mp4").title == title


def test_very_long_uploader_is_truncated() -> None:
    embed = build_embed(replace(FULL, uploader="u" * 5000), "mp4")
    uploader = embed.fields[0]
    assert uploader.name == "Uploader"
    assert uploader.value is not None
    assert len(uploader.value) == FIELD_VALUE_LIMIT


@pytest.mark.parametrize(("kind", "footer"), [("mp3", "MP3"), ("mp4", "MP4")])
def test_footer_shows_kind(kind: MediaKind, footer: str) -> None:
    assert build_embed(FULL, kind).footer.text == footer


def test_zero_views_are_shown() -> None:
    embed = build_embed(replace(FULL, view_count=0), "mp4")
    assert ("Views", "0") in [(f.name, f.value) for f in embed.fields]


def _fields(embed: discord.Embed) -> list[tuple[str | None, str | None]]:
    return [(f.name, f.value) for f in embed.fields]


@pytest.mark.parametrize("kind", ["mp3", "mp4"])
def test_clip_field_whole_seconds(kind: MediaKind) -> None:
    embed = build_embed(FULL, kind, Clip(90, 165))
    assert _fields(embed) == [
        ("Uploader", "Someone"),
        ("Duration", "1:02:05"),
        ("Clip", "1:30 → 2:45 (1:15)"),
        ("Views", "1,234,567"),
        ("Uploaded", "2024-01-31"),
    ]
    assert embed.footer.text == kind.upper()


def test_clip_field_fractional() -> None:
    embed = build_embed(FULL, "mp4", Clip(90.5, 3725))
    assert ("Clip", "1:30.5 → 1:02:05 (1:00:34.5)") in _fields(embed)


def test_clip_field_shown_without_known_duration() -> None:
    embed = build_embed(MINIMAL, "mp3", Clip(0, 10))
    assert _fields(embed) == [("Clip", "0:00 → 0:10 (0:10)")]


@pytest.mark.parametrize("kind", ["mp3", "mp4"])
def test_no_clip_embed_unchanged(kind: MediaKind) -> None:
    assert _fields(build_embed(FULL, kind, None)) == _fields(build_embed(FULL, kind))
    assert all(name != "Clip" for name, _ in _fields(build_embed(FULL, kind)))


def test_format_clip_open_end() -> None:
    assert format_clip(Clip(90, None)) == "1:30 → end"
