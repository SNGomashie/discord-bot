from dataclasses import replace

import pytest

from ninja_bot.media.models import (
    InvalidUrlError,
    MediaKind,
    TooLargeError,
    UnsupportedMediaError,
    VideoInfo,
)
from ninja_bot.media.planning import (
    HEIGHT_LADDER,
    MIN_240P_BITRATE_KBPS,
    DownloadPlan,
    format_selector,
    plan_download,
    validate_url,
)


@pytest.mark.parametrize(
    "url",
    ["https://www.youtube.com/watch?v=abc", "http://example.com/video", "HTTPS://Example.com/x"],
)
def test_validate_url_valid(url: str) -> None:
    assert validate_url(url) == url


def test_validate_url_strips_whitespace() -> None:
    assert validate_url("  https://example.com/v \n") == "https://example.com/v"


@pytest.mark.parametrize(
    "text",
    ["ftp://example.com/file", "example.com/video", "", "   ", "https://", "not a url"],
)
def test_validate_url_invalid(text: str) -> None:
    with pytest.raises(InvalidUrlError):
        validate_url(text)


MB = 1024 * 1024
INFO = VideoInfo(
    title="Clip",
    uploader=None,
    duration_s=600,
    thumbnail=None,
    webpage_url="https://example.com/v",
    view_count=None,
    upload_date=None,
    is_live=False,
    is_playlist=False,
)


@pytest.mark.parametrize(
    "info",
    [
        replace(INFO, is_live=True),
        replace(INFO, is_playlist=True),
        replace(INFO, duration_s=None),
        replace(INFO, duration_s=3601),
    ],
)
@pytest.mark.parametrize("kind", ["mp3", "mp4"])
def test_plan_download_rejects_unsupported(info: VideoInfo, kind: MediaKind) -> None:
    with pytest.raises(UnsupportedMediaError):
        plan_download(info, kind, 25 * MB, 3600)


def test_plan_download_allows_exact_max_duration() -> None:
    plan = plan_download(replace(INFO, duration_s=3600), "mp4", 100 * MB, 3600)
    assert plan.kind == "mp4"


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (100 * 192 * 1000 // 8, 192),
        (100 * 192 * 1000 // 8 - 1, 128),
        (100 * 128 * 1000 // 8, 128),
        (100 * 96 * 1000 // 8, 96),
        (100 * 64 * 1000 // 8, 64),
    ],
)
def test_plan_download_mp3_bitrate_boundaries(limit: int, expected: int) -> None:
    plan = plan_download(replace(INFO, duration_s=100), "mp3", limit, 3600)
    assert plan == DownloadPlan(kind="mp3", limit_bytes=limit, audio_bitrate_kbps=expected)


def test_plan_download_mp3_too_large_below_64kbps() -> None:
    limit = 100 * 64 * 1000 // 8 - 1
    with pytest.raises(TooLargeError):
        plan_download(replace(INFO, duration_s=100), "mp3", limit, 3600)


def test_plan_download_long_mp3_small_limit_too_large() -> None:
    with pytest.raises(TooLargeError):
        plan_download(replace(INFO, duration_s=3600), "mp3", 10 * MB, 3600)


def test_plan_download_mp4_ladder() -> None:
    plan = plan_download(INFO, "mp4", 25 * MB, 3600)
    assert plan.height_ladder == HEIGHT_LADDER == (1080, 720, 480, 360, 240)
    assert plan.audio_bitrate_kbps is None
    assert plan.limit_bytes == 25 * MB


def test_plan_download_mp4_too_large_at_240p() -> None:
    limit = 3600 * MIN_240P_BITRATE_KBPS * 1000 // 8 - 1
    with pytest.raises(TooLargeError):
        plan_download(replace(INFO, duration_s=3600), "mp4", limit, 3600)
    plan = plan_download(replace(INFO, duration_s=3600), "mp4", limit + 1, 3600)
    assert plan.height_ladder == HEIGHT_LADDER


@pytest.mark.parametrize("height", HEIGHT_LADDER)
def test_format_selector(height: int) -> None:
    plan = DownloadPlan(kind="mp4", limit_bytes=1000, height_ladder=HEIGHT_LADDER)
    size = "[filesize<=?1000][filesize_approx<=?1000]"
    assert format_selector(plan, height) == (
        f"bv*[height<={height}][ext=mp4]{size}+ba[ext=m4a]{size}"
        f"/bv*[height<={height}]{size}+ba{size}"
        f"/b[height<={height}][ext=mp4]{size}"
        f"/b[height<={height}]{size}"
    )


def test_format_selector_rejects_mp3_plan() -> None:
    with pytest.raises(ValueError):
        format_selector(DownloadPlan(kind="mp3", limit_bytes=1000, audio_bitrate_kbps=64), 240)
