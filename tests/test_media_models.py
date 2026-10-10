import datetime
from typing import Any

import pytest

from ninja_bot.media.models import (
    DownloadError,
    InvalidUrlError,
    MediaError,
    TooLargeError,
    UnsupportedMediaError,
    VideoInfo,
    format_duration,
    parse_info,
)


def test_parse_info_full() -> None:
    raw: dict[str, Any] = {
        "title": "A video",
        "uploader": "Someone",
        "duration": 125.0,
        "thumbnail": "https://img.example/t.jpg",
        "webpage_url": "https://example.com/watch?v=1",
        "view_count": 12345,
        "upload_date": "20240131",
        "is_live": False,
    }
    assert parse_info(raw) == VideoInfo(
        title="A video",
        uploader="Someone",
        duration_s=125,
        thumbnail="https://img.example/t.jpg",
        webpage_url="https://example.com/watch?v=1",
        view_count=12345,
        upload_date=datetime.date(2024, 1, 31),
        is_live=False,
        is_playlist=False,
        duration_exact_s=125.0,
    )


@pytest.mark.parametrize(
    ("duration", "truncated", "exact"),
    [
        (100.9, 100, 100.9),
        (42, 42, 42.0),
        (0.5, 0, 0.5),
        (None, None, None),
        ("100", None, None),
        (True, None, None),
        (float("nan"), None, None),
        (float("inf"), None, None),
        (10**400, 10**400, None),
    ],
)
def test_parse_info_duration(duration: Any, truncated: int | None, exact: float | None) -> None:
    info = parse_info({"duration": duration})
    assert info.duration_s == truncated
    assert info.duration_exact_s == exact


def test_parse_info_video_heights() -> None:
    raw: dict[str, Any] = {
        "formats": [
            {"height": 720, "vcodec": "avc1"},
            {"height": 360, "vcodec": "vp9"},
            {"height": 720, "vcodec": "vp9"},
            {"height": 1080},
            {"height": 480, "vcodec": "none"},
            {"height": None, "vcodec": "avc1"},
            {"height": 0, "vcodec": "avc1"},
            {"height": -5, "vcodec": "avc1"},
            {"height": 240.0, "vcodec": "avc1"},
            {"height": "144", "vcodec": "avc1"},
            {"height": True, "vcodec": "avc1"},
            {"vcodec": "avc1"},
            "not a dict",
        ]
    }
    assert parse_info(raw).video_heights == (360, 720, 1080)


@pytest.mark.parametrize("formats", [None, "x", [], [{"vcodec": "none", "height": 0}]])
def test_parse_info_video_heights_unknown(formats: Any) -> None:
    assert parse_info({"formats": formats}).video_heights == ()


def test_parse_info_minimal() -> None:
    info = parse_info({})
    assert info.title == "Untitled"
    assert info.webpage_url == ""
    assert info.uploader is None
    assert info.duration_s is None
    assert info.duration_exact_s is None
    assert info.thumbnail is None
    assert info.view_count is None
    assert info.upload_date is None
    assert info.is_live is False
    assert info.is_playlist is False


def test_parse_info_bad_upload_date_is_none() -> None:
    assert parse_info({"upload_date": "2024-01-31"}).upload_date is None


def test_parse_info_playlist() -> None:
    info = parse_info({"_type": "playlist", "title": "Mix", "webpage_url": "https://x.y/p"})
    assert info.is_playlist is True


def test_parse_info_live() -> None:
    info = parse_info({"title": "Stream", "is_live": True, "duration": None})
    assert info.is_live is True
    assert info.duration_s is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "Unknown"),
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
    ],
)
def test_format_duration(seconds: int | None, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "cls", [InvalidUrlError, UnsupportedMediaError, TooLargeError, DownloadError]
)
def test_errors_have_default_messages(cls: type[MediaError]) -> None:
    err = cls()
    assert isinstance(err, MediaError)
    assert err.message
    assert str(err) == err.message


def test_error_custom_message() -> None:
    assert TooLargeError("Too big!").message == "Too big!"
