from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.media.clip import Clip
from ninja_bot.media.downloader import YtDlpDownloader, build_ydl_options
from ninja_bot.media.models import DownloadError, TooLargeError
from ninja_bot.media.planning import HEIGHT_LADDER, DownloadPlan, format_selector

URL = "https://example.com/v"
MP3_PLAN = DownloadPlan(kind="mp3", limit_bytes=1000, audio_bitrate_kbps=128)
MP4_PLAN = DownloadPlan(kind="mp4", limit_bytes=1000, height_ladder=HEIGHT_LADDER)


class FakeYtDlpError(Exception):
    pass


def is_fake_error(exc: Exception) -> bool:
    return isinstance(exc, FakeYtDlpError)


class StubRun:
    """Writes a file of a configured size per attempt and records the options used."""

    def __init__(self, sizes: list[int], ext: str = "mp4") -> None:
        self.sizes = sizes
        self.ext = ext
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, options: dict[str, Any]) -> None:
        self.calls.append(options)
        workdir = Path(options["outtmpl"]).parent
        size = self.sizes[len(self.calls) - 1]
        (workdir / f"video.{self.ext}").write_bytes(b"x" * size)
        (workdir / f"video.{self.ext}.part").write_bytes(b"partial")


def files(path: Path) -> list[Path]:
    return sorted(path.iterdir())


def make_downloader(run: Any = None, extract: Any = None) -> YtDlpDownloader:
    def no_extract(url: str, options: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("not expected")

    def no_run(url: str, options: dict[str, Any]) -> None:
        raise AssertionError("not expected")

    return YtDlpDownloader(
        extract=extract or no_extract, run=run or no_run, is_ytdlp_error=is_fake_error
    )


def test_build_options_mp3(tmp_path: Path) -> None:
    options = build_ydl_options(MP3_PLAN, None, tmp_path)
    assert options["format"] == "bestaudio/best"
    assert options["postprocessors"] == [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}
    ]
    assert Path(options["outtmpl"]).parent == tmp_path
    assert options["noplaylist"] is True
    assert options["quiet"] is True
    assert "merge_output_format" not in options
    assert all(pp["key"] != "FFmpegVideoRemuxer" for pp in options["postprocessors"])


def test_build_options_mp4(tmp_path: Path) -> None:
    options = build_ydl_options(MP4_PLAN, 720, tmp_path)
    assert options["format"] == format_selector(MP4_PLAN, 720)
    assert options["merge_output_format"] == "mp4"
    assert Path(options["outtmpl"]).parent == tmp_path
    assert options["noplaylist"] is True
    assert options["quiet"] is True
    assert options["postprocessors"] == [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]


def test_build_options_without_clip_has_no_ranges(tmp_path: Path) -> None:
    for options in (
        build_ydl_options(MP3_PLAN, None, tmp_path),
        build_ydl_options(MP4_PLAN, 720, tmp_path),
    ):
        assert "download_ranges" not in options
        assert "force_keyframes_at_cuts" not in options


@pytest.mark.parametrize(
    ("plan", "height"),
    [
        (replace(MP3_PLAN, clip=Clip(90.5, 165.0)), None),
        (replace(MP4_PLAN, clip=Clip(90.5, 165.0)), 720),
    ],
)
def test_build_options_with_clip(plan: DownloadPlan, height: int | None, tmp_path: Path) -> None:
    options = build_ydl_options(plan, height, tmp_path)
    assert options["force_keyframes_at_cuts"] is True
    ranges = list(options["download_ranges"]({"duration": 300}, None))
    assert ranges == [{"start_time": 90.5, "end_time": 165.0}]
    unclipped = build_ydl_options(replace(plan, clip=None), height, tmp_path)
    # The format differs for mp4 clips (no whole-video size filter).
    clip_keys = {"download_ranges", "force_keyframes_at_cuts", "format"}
    assert {k: v for k, v in options.items() if k not in clip_keys} == {
        k: v for k, v in unclipped.items() if k != "format"
    }
    if height is not None:
        assert options["format"] == format_selector(plan, height)
        assert "filesize" not in options["format"]
    else:
        assert options["format"] == unclipped["format"]


def test_build_options_rejects_unresolved_clip(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_ydl_options(replace(MP3_PLAN, clip=Clip(10.0, None)), None, tmp_path)


async def test_download_clipped_mp4_steps_down(tmp_path: Path) -> None:
    run = StubRun([5000, 500])
    plan = replace(MP4_PLAN, clip=Clip(10.0, 20.0))
    output = await make_downloader(run=run).download(URL, plan, tmp_path)
    assert output.stat().st_size == 500
    assert len(run.calls) == 2
    assert all(call["force_keyframes_at_cuts"] is True for call in run.calls)
    assert files(tmp_path) == [output]


def test_build_options_mp4_requires_height(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_ydl_options(MP4_PLAN, None, tmp_path)


async def test_fetch_info_parses_result() -> None:
    seen: list[dict[str, Any]] = []

    def extract(url: str, options: dict[str, Any]) -> dict[str, Any]:
        seen.append(options)
        return {"title": "Clip", "webpage_url": url, "duration": 42}

    info = await make_downloader(extract=extract).fetch_info(URL)
    assert info.title == "Clip"
    assert info.duration_s == 42
    assert seen == [{"noplaylist": True, "quiet": True, "no_warnings": True}]


async def test_fetch_info_wraps_ytdlp_error() -> None:
    def extract(url: str, options: dict[str, Any]) -> dict[str, Any]:
        raise FakeYtDlpError("ERROR: [youtube] secret internals")

    with pytest.raises(DownloadError) as exc_info:
        await make_downloader(extract=extract).fetch_info(URL)
    assert "internals" not in exc_info.value.message


async def test_download_mp3_fits(tmp_path: Path) -> None:
    run = StubRun([500], ext="mp3")
    path = await make_downloader(run=run).download(URL, MP3_PLAN, tmp_path)
    assert path == tmp_path / "video.mp3"
    assert len(run.calls) == 1
    assert files(tmp_path) == [path]


async def test_download_mp3_too_large(tmp_path: Path) -> None:
    run = StubRun([1001], ext="mp3")
    with pytest.raises(TooLargeError):
        await make_downloader(run=run).download(URL, MP3_PLAN, tmp_path)
    assert len(run.calls) == 1
    assert files(tmp_path) == []


async def test_download_mp4_first_rung_fits(tmp_path: Path) -> None:
    run = StubRun([1000])
    path = await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert path.stat().st_size == 1000
    assert run.calls[0]["format"] == format_selector(MP4_PLAN, 1080)


async def test_download_mp4_steps_down(tmp_path: Path) -> None:
    run = StubRun([5000, 2000, 800])
    path = await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert path.stat().st_size == 800
    assert [c["format"] for c in run.calls] == [
        format_selector(MP4_PLAN, h) for h in (1080, 720, 480)
    ]
    assert files(tmp_path) == [path]


async def test_download_mp4_nothing_fits(tmp_path: Path) -> None:
    run = StubRun([5000] * len(HEIGHT_LADDER))
    with pytest.raises(TooLargeError):
        await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert len(run.calls) == len(HEIGHT_LADDER)
    assert files(tmp_path) == []


async def test_download_keeps_preexisting_files(tmp_path: Path) -> None:
    existing = tmp_path / "keep.txt"
    existing.write_text("mine")
    run = StubRun([5000] * len(HEIGHT_LADDER))
    with pytest.raises(TooLargeError):
        await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert files(tmp_path) == [existing]


async def test_download_wraps_ytdlp_error_and_cleans_up(tmp_path: Path) -> None:
    def run(url: str, options: dict[str, Any]) -> None:
        (tmp_path / "video.mp4.part").write_bytes(b"partial")
        raise FakeYtDlpError("ERROR: boom")

    with pytest.raises(DownloadError):
        await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert files(tmp_path) == []


async def test_download_no_output_file(tmp_path: Path) -> None:
    def run(url: str, options: dict[str, Any]) -> None:
        pass

    with pytest.raises(DownloadError):
        await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)


async def test_download_wrong_extension_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = StubRun([500], ext="webm")
    with caplog.at_level("INFO", logger="ninja_bot.media.downloader"):
        with pytest.raises(DownloadError) as exc_info:
            await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert ".webm" in exc_info.value.message
    assert ".mp4" in exc_info.value.message
    assert "produced no file" not in exc_info.value.message
    assert ".webm" in caplog.text
    assert len(run.calls) == 1
    assert files(tmp_path) == []


async def test_download_no_output_file_message(tmp_path: Path) -> None:
    def run(url: str, options: dict[str, Any]) -> None:
        (tmp_path / "video.webm.part").write_bytes(b"partial")

    with pytest.raises(DownloadError) as exc_info:
        await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert "produced no file" in exc_info.value.message


async def test_download_mp4_output_succeeds(tmp_path: Path) -> None:
    run = StubRun([500], ext="mp4")
    path = await make_downloader(run=run).download(URL, MP4_PLAN, tmp_path)
    assert path == tmp_path / "video.mp4"
    assert files(tmp_path) == [path]
